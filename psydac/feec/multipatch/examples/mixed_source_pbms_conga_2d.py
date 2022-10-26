from mpi4py import MPI

import os
import numpy as np
from collections import OrderedDict

from sympy import lambdify

from scipy.sparse import bmat
from scipy.sparse.linalg import spsolve

from sympde.calculus import dot
from sympde.topology import element_of
from sympde.expr.expr import LinearForm
from sympde.expr.expr import integral
from sympde.topology import Derham

from psydac.api.settings import PSYDAC_BACKENDS

from psydac.feec.pull_push import pull_2d_h1, pull_2d_hcurl, pull_2d_l2

from psydac.feec.multipatch.api                                 import discretize
from psydac.feec.multipatch.fem_linear_operators                import IdLinearOperator
from psydac.feec.multipatch.operators                           import HodgeOperator
from psydac.feec.multipatch.plotting_utilities                  import plot_field
from psydac.feec.multipatch.multipatch_domain_utilities         import build_multipatch_domain
from psydac.feec.multipatch.examples.ppc_test_cases             import get_source_and_sol_for_magnetostatic_pbm
from psydac.feec.multipatch.examples.hcurl_eigen_pbms_conga_2d  import get_eigenvalues
from psydac.feec.multipatch.utils_conga_2d                      import DiagGrid, P0_phys, P1_phys, get_Vh_diags_for
from psydac.feec.multipatch.utilities                           import time_count
from psydac.linalg.utilities                                    import array_to_stencil
from psydac.fem.basic                                           import FemField


def solve_magnetostatic_pbm(
        nc=4, deg=4, domain_name='pretzel_f', backend_language=None, source_proj='P_L2_wcurl_J',
        source_type='dipole_J', bc_type='metallic',
        gamma0_h=10., gamma1_h=10.,
        dim_harmonic_space=0,
        project_solution=False,
        plot_source=False, plot_dir=None, hide_plots=True,
        cb_min_sol=None, cb_max_sol=None, u_cf_levels=50, skip_plot_titles=False,
        m_load_dir="", sol_filename="", sol_ref_filename="",
        ref_nc=None, ref_deg=None,
):
    """
    solver for a magnetostatic problem

          div B = 0
         curl B = j

    written in the form of a mixed problem: find p in H1, u in H(curl), such that

          G^* u = f_scal     on \Omega
      G p + A u = f_vect     on \Omega

    with operators

      G:   p -> grad p
      G^*: u -> -div u
      A:   u -> curl curl u

    and sources

      f_scal = 0
      f_vect = curl j

    -- then the solution u = (Bx, By) satisfies the original magnetostatic equation, see e.g.
        Beirão da Veiga, Brezzi, Dassi, Marini and Russo, Virtual Element approx of 2D magnetostatic pbms, CMAME 327 (2017)

    Here the operators G and A are discretized with

      Gh: V0h -> V1h  and  Ah: V1h -> V1h

    in a broken-FEEC approach involving a discrete sequence on a 2D multipatch domain \Omega,

      V0h  --grad->  V1h  -—curl-> V2h

    and boundary conditions to be specified (see the multi-patch paper for details).

    Harmonic constraint: if dim_harmonic_space > 0, a constraint is added, of the form

        u in H^\perp

    where H = ker(L) is the kernel of the Hodge-Laplace operator L = curl curl u  - grad div

    Note: if source_proj == 'P_L2_wcurl_J' then a scalar J is given and we define the V1h part of the discrete source as
    l(v) := <curl_h v, J>

    :param nc: nb of cells per dimension, in each patch
    :param deg: coordinate degree in each patch
    :param gamma0_h: jump penalization parameter in V0h
    :param gamma1_h: jump penalization parameter in V1h
    :param source_proj: approximation operator for the source, possible values are 'P_geom' or 'P_L2'
    :param source_type: must be implemented as a test-case
    :param u_cf_levels: nb of contourf levels for u
    :param bc_type: 'metallic' or 'pseudo-vacuum' -- see details in multi-patch paper
    :param m_load_dir: directory for matrix storage
    """
    
    diags = {} 
    ncells = [nc, nc]
    degree = [deg,deg]

    # if backend_language is None:
    #     if domain_name in ['pretzel', 'pretzel_f'] and nc > 8:
    #         backend_language='numba'
    #     else:
    #         backend_language='python'
    # print('[note: using '+backend_language+ ' backends in discretize functions]')
    assert bc_type in ['metallic', 'pseudo-vacuum']

    print('---------------------------------------------------------------------------------------------------------')
    print('Starting solve_mixed_source_pbm function with: ')
    print(' ncells = {}'.format(ncells))
    print(' degree = {}'.format(degree))
    print(' domain_name = {}'.format(domain_name))
    print(' source_proj = {}'.format(source_proj))
    print(' bc_type = {}'.format(bc_type))
    print(' backend_language = {}'.format(backend_language))
    print('---------------------------------------------------------------------------------------------------------')

    print()
    print(' -- building discrete spaces and operators  --')

    t_stamp = time_count()
    print(' .. multi-patch domain...')
    domain = build_multipatch_domain(domain_name=domain_name)
    mappings = OrderedDict([(P.logical_domain, P.mapping) for P in domain.interior])
    mappings_list = list(mappings.values())

    t_stamp = time_count(t_stamp)
    print(' .. derham sequence...')
    derham  = Derham(domain, ["H1", "Hcurl", "L2"])

    t_stamp = time_count(t_stamp)
    print(' .. discrete domain...')
    domain_h = discretize(domain, ncells=ncells)

    t_stamp = time_count(t_stamp)
    print(' .. discrete derham sequence...')
    derham_h = discretize(derham, domain_h, degree=degree, backend=PSYDAC_BACKENDS[backend_language])

    t_stamp = time_count(t_stamp)
    print(' .. commuting projection operators...')
    nquads = [4*(d + 1) for d in degree]
    P0, P1, P2 = derham_h.projectors(nquads=nquads)

    t_stamp = time_count(t_stamp)
    print(' .. multi-patch spaces...')
    V0h = derham_h.V0
    V1h = derham_h.V1
    V2h = derham_h.V2
    print('dim(V0h) = {}'.format(V0h.nbasis))
    print('dim(V1h) = {}'.format(V1h.nbasis))
    print('dim(V2h) = {}'.format(V2h.nbasis))
    diags['ndofs_V0'] = V0h.nbasis
    diags['ndofs_V1'] = V1h.nbasis
    diags['ndofs_V2'] = V2h.nbasis

    t_stamp = time_count(t_stamp)
    print(' .. Id operator and matrix...')
    I1 = IdLinearOperator(V1h)
    I1_m = I1.to_sparse_matrix()

    t_stamp = time_count(t_stamp)
    print(' .. Hodge operators...')
    # multi-patch (broken) linear operators / matrices
    H0 = HodgeOperator(V0h, domain_h, backend_language=backend_language, load_dir=m_load_dir, load_space_index=0)
    H1 = HodgeOperator(V1h, domain_h, backend_language=backend_language, load_dir=m_load_dir, load_space_index=1)
    H2 = HodgeOperator(V2h, domain_h, backend_language=backend_language, load_dir=m_load_dir, load_space_index=2)

    H0_m  = H0.to_sparse_matrix()            # = mass matrix of V0
    dH0_m = H0.get_dual_sparse_matrix()    # = inverse mass matrix of V0
    H1_m  = H1.to_sparse_matrix()  # = mass matrix of V1
    dH1_m = H1.get_dual_sparse_matrix()              # = inverse mass matrix of V1
    H2_m  = H2.to_sparse_matrix()  # = mass matrix of V2
    dH2_m = H2.get_dual_sparse_matrix()              # = inverse mass matrix of V2
    t_stamp = time_count(t_stamp)
 
    M0_m = H0_m
    M1_m = H1_m

    hom_bc = (bc_type == 'pseudo-vacuum')  #  /!\  here u = B is in H(curl), not E  /!\
    print('with hom_bc = {}'.format(hom_bc))

    print(' .. conforming Projection operators...')
    # conforming Projections (should take into account the boundary conditions of the continuous deRham sequence)
    cP0 = derham_h.conforming_projection(space='V0', hom_bc=hom_bc, backend_language=backend_language, load_dir=m_load_dir)
    cP1 = derham_h.conforming_projection(space='V1', hom_bc=hom_bc, backend_language=backend_language, load_dir=m_load_dir)
    cP0_m = cP0.to_sparse_matrix()
    cP1_m = cP1.to_sparse_matrix()

    t_stamp = time_count(t_stamp)
    print(' .. broken differential operators...')
    # broken (patch-wise) differential operators
    bD0, bD1 = derham_h.broken_derivatives_as_operators
    bD0_m = bD0.to_sparse_matrix()
    bD1_m = bD1.to_sparse_matrix()

    I0_m = IdLinearOperator(V0h).to_sparse_matrix()
    I1_m = IdLinearOperator(V1h).to_sparse_matrix()

    if not os.path.exists(plot_dir):
        os.makedirs(plot_dir)

    # Conga (projection-based) operator matrices
    print('.. grad matrix...')
    G_m = bD0_m @ cP0_m
    tG_m = H1_m @ G_m  # grad: V0h -> tV1h

    print('.. curl-curl stiffness matrix...')
    C_m = bD1_m @ cP1_m
    CC_m = C_m.transpose() @ H2_m @ C_m

    # jump stabilization operators:
    JP0_m = I0_m - cP0_m
    S0_m = JP0_m.transpose() @ H0_m @ JP0_m

    JP1_m = I1_m - cP1_m
    S1_m = JP1_m.transpose() @ H1_m @ JP1_m

    if not hom_bc:
        # very small regularization to avoid constant p=1 in the kernel
        # reg_S0_m = 1e-16 * M0_m + gamma0_h * S0_m
        print('LARGE EPS = 1')
        eps = 1
        reg_S0_m = eps * M0_m + gamma0_h * S0_m
    else:
        reg_S0_m = gamma0_h * S0_m

    hf_cs = []
    if dim_harmonic_space > 0:

        print('.. computing the harmonic fields...')
        gamma_Lh = 10  # penalization value should not change the kernel

        GD_m = - tG_m @ dH0_m @ G_m.transpose() @ H1_m   # todo: check with paper
        L_m = CC_m - GD_m + gamma_Lh * S1_m
        eigenvalues, eigenvectors = get_eigenvalues(dim_harmonic_space+1, 1e-6, L_m, H1_m)

        for i in range(dim_harmonic_space):
            lambda_i =  eigenvalues[i]
            print(" .. storing eigenmode #{}, with eigenvalue = {}".format(i, lambda_i))
            # check:
            if abs(lambda_i) > 1e-8:
                print(" ****** WARNING! this eigenvalue should be 0!   ****** ")
            hf_cs.append(eigenvectors[:,i])

        # matrix of the coefs of the harmonic fields (Lambda^H_i) in the basis (Lambda_i), in the form:
        #   hf_m = (c^H_{i,j})_{i < dim_harmonic_space, j < dim_V1}  such that  Lambda^H_i = sum_j c^H_{i,j} Lambda^1_j
        hf_m = bmat(hf_cs).transpose()
        MH_m = M1_m @ hf_m

        # check:
        lambda_i = eigenvalues[dim_harmonic_space]  # should be the first positive eigenvalue of L_h
        if abs(lambda_i) < 1e-4:
            print(" ****** Warning -- something is probably wrong: ")
            print(" ******            eigenmode #{} should have positive eigenvalue: {}".format(dim_harmonic_space, lambda_i))

        print(' .. computing the full operator matrix with harmonic constraint...')
        A_m = bmat([[ reg_S0_m,        tG_m.transpose(),  None ],
                    [     tG_m,  CC_m + gamma1_h * S1_m,  MH_m ],
                    [     None,        MH_m.transpose(),  None ]])

    else:
        print(' .. computing the full operator matrix without harmonic constraint...')

        A_m = bmat([[ reg_S0_m,        tG_m.transpose() ],
                    [     tG_m,  CC_m + gamma1_h * S1_m ]])

    # compute approximate source:
    #   ff_h = (f0_h, f1_h) = (P0_h f_scal, P1_h f_vect)  with projection operators specified by source_proj
    #   and dual-basis coefficients in column array  bb_c = (b0_c, b1_c)
    # note: f1_h may also be defined through the special option 'P_L2_wcurl_J' for magnetostatic problems

    t_stamp = time_count(t_stamp)
    print()
    print(' -- getting source --')
    f_scal, f_vect, j_scal, u_ex = get_source_and_sol_for_magnetostatic_pbm(source_type=source_type, domain=domain, domain_name=domain_name)
    f0_c = f1_c = j2_c = None
    assert source_proj in ['P_geom', 'P_L2', 'P_L2_wcurl_J']

    if f_scal is None:
        tilde_f0_c = np.zeros(V0h.nbasis)
    else:
        print(' .. approximating the V0 source with '+source_proj)
        if source_proj == 'P_geom':
            f0_h = P0_phys(f_scal, P0, domain, mappings_list)
            f0_c = f0_h.coeffs.toarray()
            tilde_f0_c = H0_m.dot(f0_c)
        else:
            # L2 proj
            tilde_f0_c = derham_h.get_dual_dofs(space='V0', f=f_scal, backend_language=backend_language, return_format='numpy_array')

    if source_proj == 'P_L2_wcurl_J':
        if j_scal is None:
            tilde_j2_c = np.zeros(V2h.nbasis)
            tilde_f1_c = np.zeros(V1h.nbasis)
        else:
            print(' .. approximating the V1 source as a weak curl of j_scal')
            tilde_j2_c = derham_h.get_dual_dofs(space='V2', f=j_scal, backend_language=backend_language, return_format='numpy_array')
            tilde_f1_c = C_m.transpose().dot(tilde_j2_c)
    elif f_vect is None:
        tilde_f1_c  = np.zeros(V1h.nbasis)
    else:
        print(' .. approximating the V1 source with '+source_proj)
        if source_proj == 'P_geom':
            f1_h = P1_phys(f_vect, P1, domain, mappings_list)
            f1_c = f1_h.coeffs.toarray()
            tilde_f1_c = H1_m.dot(f1_c)
        else:
            assert source_proj == 'P_L2'
            tilde_f1_c = derham_h.get_dual_dofs(space='V1', f=f_vect, backend_language=backend_language, return_format='numpy_array')

    if plot_source:
        if f0_c is None:
            f0_c = dH0_m.dot(tilde_f0_c)
        plot_field(numpy_coeffs=f0_c, Vh=V0h, space_kind='h1', domain=domain, title='f0_h with P = '+source_proj,
                   filename=plot_dir+'/f0h_'+source_proj+'.png', hide_plot=hide_plots)
        if f1_c is None:
            f1_c = dH1_m.dot(tilde_f1_c)
        if skip_plot_titles:
            title = ''
        else:
            title = 'f1_h with P = '+source_proj
        plot_field(numpy_coeffs=f1_c, Vh=V1h, space_kind='hcurl', plot_type='vector_field', domain=domain, title=title,
                   filename=plot_dir+'/f1h_'+source_proj+'.pdf', hide_plot=hide_plots)
        if source_proj == 'P_L2_wcurl_J':
            if j2_c is None:
                j2_c = dH2_m.dot(tilde_j2_c)
            if skip_plot_titles:
                title = ''
            else:
                title = 'jh in V2h'
            plot_field(numpy_coeffs=j2_c, cmap='viridis', plot_type='components', Vh=V2h, space_kind='l2', domain=domain, 
                       title=title,
                       filename=plot_dir+'/j2h.pdf', hide_plot=hide_plots)

    print(" .. building block RHS")
    if dim_harmonic_space > 0:
        tilde_h_c = np.zeros(dim_harmonic_space)  # harmonic part of the rhs
        b_c = np.block([tilde_f0_c, tilde_f1_c, tilde_h_c])
    else:
        b_c = np.block([tilde_f0_c, tilde_f1_c])

    print()
    print(' -- ref solution: writing values on diag grid  --')
    diag_grid = DiagGrid(mappings=mappings, N_diag=100)
    if u_ex is not None:
        print(' .. u_ex is known:')
        print('    setting uh_ref = P_geom(u_ex)')
        uh_ref = P1_phys(u_ex, P1, domain, mappings_list)
        diag_grid.write_sol_ref_values(uh_ref, space='V1')
    else:
        print(' .. u_ex is unknown:')
        print('    importing uh_ref in ref_V1h from file {}...'.format(sol_ref_filename))
        diag_grid.create_ref_fem_spaces(domain=domain, ref_nc=ref_nc, ref_deg=ref_deg)
        diag_grid.import_ref_sol_from_coeffs(sol_ref_filename, space='V1')
        diag_grid.write_sol_ref_values(space='V1')


    # direct solve with scipy spsolve ------------------------------
    print(' -- solving source problem with scipy.spsolve...')
    sol_c = spsolve(A_m.asformat('csr'), b_c)
    #   ------------------------------------------------------------
    ph_c = sol_c[:V0h.nbasis]
    uh_c = sol_c[V0h.nbasis:V0h.nbasis+V1h.nbasis]
    hh_c = np.zeros(V1h.nbasis)
    if dim_harmonic_space > 0:
        # compute the harmonic part (h) of the solution
        hh_hbcoefs = sol_c[V0h.nbasis+V1h.nbasis:]  # coefs of the harmonic part, in the basis of the harmonic fields
        assert len(hh_hbcoefs) == dim_harmonic_space
        for i in range(dim_harmonic_space):
            hi_c = hf_cs[i]  # coefs the of the i-th harmonic field, in the B/M spline basis of V1h
            hh_c += hh_hbcoefs[i]*hi_c

    if project_solution:
        print(' .. projecting the homogeneous solution on the conforming problem space...')
        uh_c = cP1_m.dot(uh_c)
        u_name = r'$P^1_h B_h$'
        ph_c = cP0_m.dot(ph_c)
        p_name = r'$P^0_h p_h$'
    else:
        u_name = r'$B_h$'
        p_name = r'$p_h$'

    uh = FemField(V1h, coeffs=array_to_stencil(uh_c, V1h.vector_space))
    t_stamp = time_count(t_stamp)

    print()
    print(' -- plots and diagnostics  --')
    if plot_dir:
        print(' .. plotting the FEM solution...')
        params_str = 'gamma0_h={}_gamma1_h={}'.format(gamma0_h, gamma1_h)
        title = r'solution {} (amplitude)'.format(p_name)
        plot_field(numpy_coeffs=ph_c, Vh=V0h, space_kind='h1', plot_type='amplitude',
                domain=domain, title=title, filename=plot_dir+'/'+params_str+'_ph.png', hide_plot=hide_plots)
        title = r'solution $h_h$ (amplitude)'
        plot_field(numpy_coeffs=hh_c, Vh=V1h, space_kind='hcurl', plot_type='amplitude',
                domain=domain, title=title, filename=plot_dir+'/'+params_str+'_hh.png', hide_plot=hide_plots)
        if skip_plot_titles:
            title = ''
        else:
            title = r'solution {} (amplitude)'.format(u_name)
        plot_field(numpy_coeffs=uh_c, Vh=V1h, space_kind='hcurl', plot_type='amplitude',
                cb_min=cb_min_sol, cb_max=cb_max_sol, cf_levels=u_cf_levels,
                domain=domain, title=title, filename=plot_dir+'/'+params_str+'_uh.pdf', hide_plot=hide_plots)
        if skip_plot_titles:
            title = ''
        else:
            title = r'solution {} (vector field)'.format(u_name)
        plot_field(numpy_coeffs=uh_c, Vh=V1h, space_kind='hcurl', plot_type='vector_field',
                domain=domain, title=title, filename=plot_dir+'/'+params_str+'_uh_vf.pdf', hide_plot=hide_plots)
        title = r'solution {} (components)'.format(u_name)
        plot_field(numpy_coeffs=uh_c, Vh=V1h, space_kind='hcurl', plot_type='components',
                domain=domain, title=title, filename=plot_dir+'/'+params_str+'_uh_xy.png', hide_plot=hide_plots)

    if sol_filename:
        print(' .. saving u (=B) solution coeffs to file {}'.format(sol_filename))
        np.save(sol_filename, uh_c)

    time_count(t_stamp)
    
    # diagnostics: errors        
    err_diags = diag_grid.get_diags_for(v=uh, space='V1')
    for key, value in err_diags.items():
        diags[key] = value

    if u_ex is not None:
        check_diags = get_Vh_diags_for(v=uh, v_ref=uh_ref, M_m=H1_m, msg='error between Ph(u_ex) and u_h')
        diags['norm_Pu_ex'] = check_diags['sol_ref_norm']
        diags['rel_l2_error_in_Vh'] = check_diags['rel_l2_error']

    return diags


if __name__ == '__main__':

    t_stamp_full = time_count()

    bc_type = 'metallic'
    # bc_type = 'pseudo-vacuum'
    source_type = 'dipole_J'

    source_proj = 'P_L2_wcurl_J'

    domain_name = 'pretzel_f'
    dim_harmonic_space = 3

    nc = 16
    deg = 4
    # nc = 10
    # deg = 2

    # domain_name = 'curved_L_shape'
    # dim_harmonic_space = 0

    # nc = 2
    # deg = 2

    run_dir = '{}_{}_bc={}_nc={}_deg={}'.format(domain_name, source_type, bc_type, nc, deg)
    m_load_dir = 'matrices_{}_nc={}_deg={}'.format(domain_name, nc, deg)
    solve_magnetostatic_pbm(
        nc=nc, deg=deg,
        domain_name=domain_name,
        source_type=source_type,
        source_proj=source_proj,
        bc_type=bc_type,
        backend_language='pyccel-gcc',
        dim_harmonic_space=dim_harmonic_space,
        plot_source=True,
        plot_dir='./plots/magnetostatic_runs/'+run_dir,
        hide_plots=True,
        m_load_dir=m_load_dir
    )

    time_count(t_stamp_full, msg='full program')
