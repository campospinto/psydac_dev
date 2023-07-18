import os
from mpi4py import MPI

import numpy as np
import matplotlib.pyplot as plt
from collections import OrderedDict
from sympde.topology     import Derham

from psydac.feec.multipatch.api                         import discretize
from psydac.api.settings                                import PSYDAC_BACKENDS
from psydac.feec.multipatch.fem_linear_operators        import IdLinearOperator
from psydac.feec.multipatch.operators                   import HodgeOperator
from psydac.feec.multipatch.multipatch_domain_utilities import build_multipatch_domain
from psydac.feec.multipatch.plotting_utilities          import plot_field
from psydac.feec.multipatch.utilities                   import time_count, get_run_dir, get_plot_dir, get_mat_dir, get_sol_dir, diag_fn
from psydac.feec.multipatch.utils_conga_2d              import write_diags_to_file

from sympde.topology      import Square    
from sympde.topology      import IdentityMapping, PolarMapping
from psydac.fem.vector import ProductFemSpace

from scipy.sparse.linalg import spilu, lgmres
from scipy.sparse.linalg import LinearOperator, eigsh, minres
from scipy.sparse          import csr_matrix
from scipy.linalg        import norm

from psydac.linalg.utilities import array_to_psydac
from psydac.fem.basic        import FemField

from psydac.feec.multipatch.non_matching_multipatch_domain_utilities import create_square_domain
from psydac.feec.multipatch.non_matching_operators import construct_V1_conforming_projection

from psydac.api.postprocessing import OutputManager, PostProcessManager

#from said
from scipy.sparse.linalg import spsolve, inv

from sympde.calculus      import grad, dot, curl, cross
from sympde.calculus      import minus, plus
from sympde.topology      import VectorFunctionSpace
from sympde.topology      import elements_of
from sympde.topology      import NormalVector
from sympde.topology      import Square
from sympde.topology      import IdentityMapping, PolarMapping
from sympde.expr.expr     import LinearForm, BilinearForm
from sympde.expr.expr     import integral
from sympde.expr.expr     import Norm
from sympde.expr.equation import find, EssentialBC

from psydac.api.tests.build_domain   import build_pretzel
from psydac.fem.basic                import FemField
from psydac.api.settings             import PSYDAC_BACKEND_GPYCCEL
from psydac.feec.pull_push           import pull_2d_hcurl

def hcurl_solve_eigen_pbm_multipatch_nc(ncells=[[2,2], [2,2]], degree=[3,3], domain=[[0, np.pi],[0, np.pi]], domain_name='refined_square', backend_language='pyccel-gcc', mu=1, nu=0, gamma_h=0,
                          generalized_pbm=False, sigma=None, ref_sigmas=[], nb_eigs_solve=8, nb_eigs_plot=5, skip_eigs_threshold=1e-7,
                          plot_dir=None, hide_plots=True, m_load_dir="",):

    diags = {}
    
    if sigma is None:
        raise ValueError('please specify a value for sigma')

    print('---------------------------------------------------------------------------------------------------------')
    print('Starting hcurl_solve_eigen_pbm function with: ')
    print(' ncells = {}'.format(ncells))
    print(' degree = {}'.format(degree))
    print(' domain_name = {}'.format(domain_name))
    print(' backend_language = {}'.format(backend_language))
    print('---------------------------------------------------------------------------------------------------------')
    t_stamp = time_count()
    print('building symbolic and discrete domain...')

    int_x, int_y = domain
    
    if domain_name == 'refined_square' or domain_name =='square_L_shape':
        domain = create_square_domain(ncells, int_x, int_y, mapping='identity')
        ncells_h = {patch.name: [ncells[int(patch.name[2])][int(patch.name[4])], ncells[int(patch.name[2])][int(patch.name[4])]] for patch in domain.interior}
    elif domain_name == 'curved_L_shape':
        domain = create_square_domain(ncells, int_x, int_y, mapping='polar')
        ncells_h = {patch.name: [ncells[int(patch.name[2])][int(patch.name[4])], ncells[int(patch.name[2])][int(patch.name[4])]] for patch in domain.interior}
    elif domain_name == 'pretzel_f':
        domain = build_multipatch_domain(domain_name=domain_name) 
        ncells_h = {patch.name: [ncells[i], ncells[i]] for (i,patch) in enumerate(domain.interior)}

    else:
        ValueError("Domain not defined.")

   # domain = build_multipatch_domain(domain_name = 'curved_L_shape')
   #                     
   # ncells = np.array([4,8,4])
   # ncells_h = {patch.name: [ncells[i], ncells[i]] for (i,patch) in enumerate(domain.interior)}
    mappings = OrderedDict([(P.logical_domain, P.mapping) for P in domain.interior])
    mappings_list = list(mappings.values())


    t_stamp = time_count(t_stamp)
    print(' .. discrete domain...')
    domain_h = discretize(domain, ncells=ncells_h)   # Vh space

    print('building symbolic and discrete derham sequences...')
    t_stamp = time_count()
    print(' .. derham sequence...')
    derham  = Derham(domain, ["H1", "Hcurl", "L2"])

    t_stamp = time_count(t_stamp)
    print(' .. discrete derham sequence...')
    derham_h = discretize(derham, domain_h, degree=degree)

    
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
    print('building the discrete operators:')
    #print('commuting projection operators...')
    #nquads = [4*(d + 1) for d in degree]
    #P0, P1, P2 = derham_h.projectors(nquads=nquads)

    I1 = IdLinearOperator(V1h)
    I1_m = I1.to_sparse_matrix()

    t_stamp = time_count(t_stamp)
    print('Hodge operators...')
    # multi-patch (broken) linear operators / matrices
    #H0 = HodgeOperator(V0h, domain_h, backend_language=backend_language, load_dir=m_load_dir, load_space_index=0)
    H1 = HodgeOperator(V1h, domain_h, backend_language=backend_language, load_dir=m_load_dir, load_space_index=1)
    H2 = HodgeOperator(V2h, domain_h, backend_language=backend_language, load_dir=m_load_dir, load_space_index=2)

    #H0_m  = H0.to_sparse_matrix()           # = mass matrix of V0
    #dH0_m = H0.get_dual_sparse_matrix()     # = inverse mass matrix of V0
    H1_m  = H1.to_sparse_matrix()           # = mass matrix of V1
    dH1_m = H1.get_dual_Hodge_sparse_matrix()     # = inverse mass matrix of V1
    H2_m  = H2.to_sparse_matrix()           # = mass matrix of V2
    dH2_m = H2.get_dual_Hodge_sparse_matrix()
    
    t_stamp = time_count(t_stamp)
    print('conforming projection operators...')
    # conforming Projections (should take into account the boundary conditions of the continuous deRham sequence)
    cP0_m = None
    cP1_m = construct_V1_conforming_projection(V1h, domain_h, hom_bc=True)

    t_stamp = time_count(t_stamp)
    print('broken differential operators...')
    bD0, bD1 = derham_h.broken_derivatives_as_operators
    #bD0_m = bD0.to_sparse_matrix()
    bD1_m = bD1.to_sparse_matrix()

    t_stamp = time_count(t_stamp)
    print('converting some matrices to csr format...')

    H1_m = H1_m.tocsr()
    dH1_m = dH1_m.tocsr()
    H2_m = H2_m.tocsr()
    cP1_m = cP1_m.tocsr()
    bD1_m = bD1_m.tocsr()    

    if not os.path.exists(plot_dir):
        os.makedirs(plot_dir)

    print('computing the full operator matrix...')
    A_m = np.zeros_like(H1_m) 

    # Conga (projection-based) stiffness matrices
    if mu != 0:
        # curl curl:
        t_stamp = time_count(t_stamp)
        print('mu = {}'.format(mu))
        print('curl-curl stiffness matrix...')
        
        pre_CC_m = bD1_m.transpose() @ dH2_m @ bD1_m
        CC_m = cP1_m.transpose() @ pre_CC_m @ cP1_m  # Conga stiffness matrix
        A_m += mu * CC_m 

    # jump stabilization in V1h:
    if gamma_h != 0 or generalized_pbm:
        t_stamp = time_count(t_stamp)
        print('jump stabilization matrix...')
        jump_stab_m = I1_m - cP1_m
        JS_m = jump_stab_m.transpose() @ dH1_m @ jump_stab_m
        
    if generalized_pbm:
        print('adding jump stabilization to RHS of generalized eigenproblem...')
        B_m = cP1_m.transpose() @ dH1_m @ cP1_m + JS_m
    else:
        B_m = dH1_m
    

    t_stamp = time_count(t_stamp)
    print('solving matrix eigenproblem...')
    all_eigenvalues, all_eigenvectors_transp = get_eigenvalues(nb_eigs_solve, sigma, A_m, B_m)
    #Eigenvalue processing
    t_stamp = time_count(t_stamp)
    print('sorting out eigenvalues...')
    zero_eigenvalues = []
    if skip_eigs_threshold is not None:
        eigenvalues = []
        eigenvectors = []
        for val, vect in zip(all_eigenvalues, all_eigenvectors_transp.T):
            if abs(val) < skip_eigs_threshold: 
                zero_eigenvalues.append(val)
                # we skip the eigenvector
            else:
                eigenvalues.append(val)
                eigenvectors.append(vect)
    else:
        eigenvalues = all_eigenvalues
        eigenvectors = all_eigenvectors_transp.T

    for k, val in enumerate(eigenvalues):
        diags['eigenvalue_{}'.format(k)] = val #eigenvalues[k]
    
    for k, val in enumerate(zero_eigenvalues):
        diags['skipped eigenvalue_{}'.format(k)] = val

    t_stamp = time_count(t_stamp)
    print('plotting the eigenmodes...')     

   # OM = OutputManager('spaces.yml', 'fields.h5')
   # OM.add_spaces(V1h=V1h)

    nb_eigs = len(eigenvalues)
    for i in range(min(nb_eigs_plot, nb_eigs)):
        OM = OutputManager(plot_dir+'/spaces.yml', plot_dir+'/fields.h5')
        OM.add_spaces(V1h=V1h)
        print('looking at emode i = {}... '.format(i))
        lambda_i  = eigenvalues[i]
        emode_i = np.real(eigenvectors[i])
        norm_emode_i = np.dot(emode_i,H1_m.dot(emode_i))
        eh_c = emode_i/norm_emode_i 
        stencil_coeffs = array_to_psydac(cP1_m @ eh_c, V1h.vector_space)
        vh = FemField(V1h, coeffs=stencil_coeffs)
        OM.set_static()
        #OM.add_snapshot(t=i , ts=0) 
        OM.export_fields(vh = vh)

        #print('norm of computed eigenmode: ', norm_emode_i)
        # plot the broken eigenmode:
        OM.export_space_info()
        OM.close()

        PM = PostProcessManager(domain=domain, space_file=plot_dir+'/spaces.yml', fields_file=plot_dir+'/fields.h5' )
        PM.export_to_vtk(plot_dir+"/eigen_{}".format(i),grid=None, npts_per_cell=[6]*2,snapshots='all', fields='vh' )
        PM.close()

        t_stamp = time_count(t_stamp)

    ### Saids Code

    V  = VectorFunctionSpace('V', domain, kind='hcurl')

    u, v, F  = elements_of(V, names='u, v, F')
    nn       = NormalVector('nn')

    I        = domain.interfaces
    boundary = domain.boundary

    kappa   = 10
    k       = 1

    jump = lambda w:plus(w)-minus(w)
    avr  = lambda w:0.5*plus(w) + 0.5*minus(w)

    expr1_I  =  cross(nn, jump(v))*curl(avr(u))\
               +k*cross(nn, jump(u))*curl(avr(v))\
               +kappa*cross(nn, jump(u))*cross(nn, jump(v))

    expr1   = curl(u)*curl(v) 
    expr1_b = -cross(nn, v) * curl(u) -k*cross(nn, u)*curl(v)  + kappa*cross(nn, u)*cross(nn, v)
    ## curl curl u = - omega**2 u 

    expr2   = dot(u,v)
    #expr2_I = kappa*cross(nn, jump(u))*cross(nn, jump(v))
    #expr2_b = -k*cross(nn, u)*curl(v) + kappa * cross(nn, u) * cross(nn, v)

    # Bilinear form a: V x V --> R
    a      = BilinearForm((u,v),  integral(domain, expr1) + integral(I, expr1_I) + integral(boundary, expr1_b))
    
    # Linear form l: V --> R
    b     = BilinearForm((u,v), integral(domain, expr2))# + integral(I, expr2_I) + integral(boundary, expr2_b))

    #+++++++++++++++++++++++++++++++
    # 2. Discretization
    #+++++++++++++++++++++++++++++++

    domain_h = discretize(domain, ncells=ncells_h)
    Vh       = discretize(V, domain_h, degree=degree)

    ah = discretize(a, domain_h, [Vh, Vh])
    Ah_m = ah.assemble().tosparse()

    bh = discretize(b, domain_h, [Vh, Vh])
    Bh_m = bh.assemble().tosparse()

    all_eigenvalues_2, all_eigenvectors_transp_2 = get_eigenvalues(nb_eigs_solve, sigma, Ah_m, Bh_m)

    #Eigenvalue processing
    t_stamp = time_count(t_stamp)
    print('sorting out eigenvalues...')
    zero_eigenvalues2 = []
    if skip_eigs_threshold is not None:
        eigenvalues2 = []
        eigenvectors2 = []
        for val, vect in zip(all_eigenvalues_2, all_eigenvectors_transp_2.T):
            if abs(val) < skip_eigs_threshold: 
                zero_eigenvalues2.append(val)
                # we skip the eigenvector
            else:
                eigenvalues2.append(val)
                eigenvectors2.append(vect)
    else:
        eigenvalues2 = all_eigenvalues_2
        eigenvectors2 = all_eigenvectors_transp_2.T
    diags['DG'] = True 
    for k, val in enumerate(eigenvalues2):
        diags['eigenvalue2_{}'.format(k)] = val #eigenvalues[k]
    
    for k, val in enumerate(zero_eigenvalues2):
        diags['skipped eigenvalue2_{}'.format(k)] = val

    t_stamp = time_count(t_stamp)
    print('plotting the eigenmodes...')     

   # OM = OutputManager('spaces.yml', 'fields.h5')
   # OM.add_spaces(V1h=V1h)

    nb_eigs = len(eigenvalues2)
    for i in range(min(nb_eigs_plot, nb_eigs)):
        OM = OutputManager(plot_dir+'/spaces2.yml', plot_dir+'/fields2.h5')
        OM.add_spaces(V1h=Vh)
        print('looking at emode i = {}... '.format(i))
        lambda_i  = eigenvalues2[i]
        emode_i = np.real(eigenvectors2[i])
        norm_emode_i = np.dot(emode_i,Bh_m.dot(emode_i))
        eh_c = emode_i/norm_emode_i 
        stencil_coeffs = array_to_psydac(eh_c, Vh.vector_space)
        vh = FemField(Vh, coeffs=stencil_coeffs)
        OM.set_static()
        #OM.add_snapshot(t=i , ts=0) 
        OM.export_fields(vh = vh)

        #print('norm of computed eigenmode: ', norm_emode_i)
        # plot the broken eigenmode:
        OM.export_space_info()
        OM.close()

        PM = PostProcessManager(domain=domain, space_file=plot_dir+'/spaces2.yml', fields_file=plot_dir+'/fields2.h5' )
        PM.export_to_vtk(plot_dir+"/eigen2_{}".format(i),grid=None, npts_per_cell=[6]*2,snapshots='all', fields='vh' )
        PM.close()

        t_stamp = time_count(t_stamp)

    return diags, eigenvalues, eigenvalues2


def get_eigenvalues(nb_eigs, sigma, A_m, M_m):
    print('-----  -----  -----  -----  -----  -----  -----  -----  -----  -----  -----  -----  -----  -----  -----  ----- ')
    print('computing {0} eigenvalues (and eigenvectors) close to sigma={1} with scipy.sparse.eigsh...'.format(nb_eigs, sigma) )
    mode = 'normal'
    which = 'LM'
    # from eigsh docstring:
    #   ncv = number of Lanczos vectors generated ncv must be greater than k and smaller than n;
    #   it is recommended that ncv > 2*k. Default: min(n, max(2*k + 1, 20))
    ncv = 4*nb_eigs
    print('A_m.shape = ', A_m.shape)
    try_lgmres = True
    max_shape_splu = 24000   # OK for nc=20, deg=6 on pretzel_f
    if A_m.shape[0] < max_shape_splu:
        print('(via sparse LU decomposition)')
        OPinv = None
        tol_eigsh = 0
    else:

        OP_m = A_m - sigma*M_m
        tol_eigsh = 1e-7
        if try_lgmres:
            print('(via SPILU-preconditioned LGMRES iterative solver for A_m - sigma*M1_m)')
            OP_spilu = spilu(OP_m, fill_factor=15, drop_tol=5e-5)
            preconditioner = LinearOperator(OP_m.shape, lambda x: OP_spilu.solve(x) )
            tol = tol_eigsh
            OPinv = LinearOperator(
                matvec=lambda v: lgmres(OP_m, v, x0=None, tol=tol, atol=tol, M=preconditioner,
                                    callback=lambda x: print('cg -- residual = ', norm(OP_m.dot(x)-v))
                                    )[0],
                shape=M_m.shape,
                dtype=M_m.dtype
            )

        else:
            # from https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.linalg.eigsh.html:
            # the user can supply the matrix or operator OPinv, which gives x = OPinv @ b = [A - sigma * M]^-1 @ b.
            # > here, minres: MINimum RESidual iteration to solve Ax=b
            # suggested in https://github.com/scipy/scipy/issues/4170
            print('(with minres iterative solver for A_m - sigma*M1_m)')
            OPinv = LinearOperator(matvec=lambda v: minres(OP_m, v, tol=1e-10)[0], shape=M_m.shape, dtype=M_m.dtype)

    eigenvalues, eigenvectors = eigsh(A_m, k=nb_eigs, M=M_m, sigma=sigma, mode=mode, which=which, ncv=ncv, tol=tol_eigsh, OPinv=OPinv)

    print("done: eigenvalues found: " + repr(eigenvalues))
    return eigenvalues, eigenvectors