# coding: utf-8

from pytest import param
from mpi4py import MPI

import os
import numpy as np
from collections import OrderedDict
import matplotlib.pyplot as plt

from sympy import lambdify, Matrix

from scipy.sparse import save_npz, load_npz
from scipy.sparse.linalg import spsolve
from scipy import special

from sympde.calculus  import dot
from sympde.topology  import element_of
from sympde.expr.expr import LinearForm
from sympde.expr.expr import integral, Norm
from sympde.topology  import Derham

from psydac.api.settings   import PSYDAC_BACKENDS
from psydac.feec.pull_push import pull_2d_hcurl

from psydac.feec.multipatch.api                         import discretize
from psydac.feec.multipatch.fem_linear_operators        import IdLinearOperator
from psydac.feec.multipatch.operators                   import HodgeOperator, get_K0_and_K0_inv, get_K1_and_K1_inv
from psydac.feec.multipatch.plotting_utilities_2          import plot_field #, write_field_to_diag_grid, 
from psydac.feec.multipatch.multipatch_domain_utilities import build_multipatch_domain, build_multipatch_rectangle
from psydac.feec.multipatch.examples.ppc_test_cases     import get_div_free_pulse, get_cavity_solution
from psydac.feec.multipatch.utils_conga_2d              import DiagGrid, P_phys_l2, P_phys_hdiv, P_phys_hcurl, P_phys_h1, get_Vh_diags_for
from psydac.feec.multipatch.utilities                   import time_count #, export_sol, import_sol
from psydac.feec.multipatch.bilinear_form_scipy         import construct_pairing_matrix
# from psydac.feec.multipatch.conf_projections_scipy      import Conf_proj_0, Conf_proj_1, Conf_proj_0_c1, Conf_proj_1_c1
import psydac.feec.multipatch.conf_projections_scipy as cps

from psydac.utilities.quadratures import gauss_lobatto

cps.mom_pres = True #False # 
cps.proj_op = 2

def solve_td_maxwell_pbm(
        method='ssc',
        nbc=4, deg=4, Nt_pertau=None, cfl=.8, tau=None,
        nb_tau=1, sol_params=None, source_is_harmonic=True,
        domain_name='pretzel_f', backend_language=None, source_proj='P_geom', source_type='manu_J',
        nb_patch_x=2, nb_patch_y=2, 
        project_sol=False, filter_source=True, quad_param=1,
        solution_type='zero', solution_proj='P_geom', 
        plot_source=False, plot_dir=None, hide_plots=True, plot_time_ranges=None, 
        plot_variables=["D", "B", "Dex", "Bex", "divD"], diag_dtau=None,
        skip_plot_titles=False,
        cb_min_sol=None, cb_max_sol=None,
        m_load_dir="",
        dry_run = False,
):
    """
    solver for the TD Maxwell problem: find E(t) in H(curl), B in L2, such that

      dt D - curl H = -J             on \Omega
      dt B + curl E = 0              on \Omega
      n x E = 0                      on \partial \Omega

    with SSC scheme on a 2D multipatch domain \Omega
    involving two strong sequences, a  and a dual 

      primal: p_V0h  --grad->  p_V1h  -—curl-> p_V2h        (with homogeneous bc's)
                                (Eh)            (Bh)

                                (Dh)            (Hh)
      dual:   d_V2h  <--div--  d_V1h  <—curl-- d_V0h        (no bc's)

    the semi-discrete level the equations read

        Ampere: 
            Hh = p_HH2 @ Bh
            dt Dh - d_CC @ Hh = - Jh         
        with
            p_HH2 = hodge:   p_V2h -> d_V0h
            d_CC  = curl:    d_V0h -> d_V1h

        Faraday:    
            Eh = d_HH1 @ Dh
            dt Bh + p_curl @ Eh = 0              
        with
            d_HH1 = hodge:   d_V1h -> p_V1h
            p_CC  = curl:    p_V1h -> p_V2h

    :param nbc: nb of cells per dimension, in each patch
    :param deg: coordinate degree in each patch
    :param source_proj: approximation operator for the source (see later)
    :param source_type: must be implemented in get_source_and_solution()
    :param m_load_dir: directory for matrix storage
    """

    if solution_type == 'cavity':
        omega = sol_params['omega']
        kx = sol_params['kx']
        ky = sol_params['ky']
    
    else:
        raise NotImplementedError
    

    diags = {}

    if dry_run:
        diags["D_error"] = np.random.random()
        diags["E_error"] = np.random.random()
        diags["B_error"] = np.random.random()
        diags["H_error"] = np.random.random()

        return diags

    ncells = [nbc, nbc]
    degree = [deg,deg]

    final_time = nb_tau * tau

    print('final_time = ', final_time)
    if plot_time_ranges is None:
        plot_time_ranges = [[0, final_time], 2]

    if diag_dtau is None:
        diag_dtau = nb_tau//10

    if m_load_dir is not None:
        pm_load_dir = m_load_dir+"primal"
        dm_load_dir = m_load_dir+"dual"
        for load_dir in [m_load_dir, pm_load_dir, dm_load_dir]:        
            if not os.path.exists(load_dir):
                os.makedirs(load_dir)

    print('---------------------------------------------------------------------------------------------------------')
    print('Starting solve_td_maxwell_pbm function with: ')
    print(' domain_name = {}'.format(domain_name))
    if domain_name == 'multipatch_rectangle':
        print(' nb_patches = [{},{}]'.format(nb_patch_x, nb_patch_y))
    print(' ncells = {}'.format(ncells))
    print(' degree = {}'.format(degree))
    print(' solution_type = {}'.format(solution_type))
    print(' solution_proj = {}'.format(solution_proj))
    print(' source_type = {}'.format(source_type))
    print(' source_proj = {}'.format(source_proj))
    print(' backend_language = {}'.format(backend_language))
    print('---------------------------------------------------------------------------------------------------------')

    debug = False

    print()
    print(' -- building discrete spaces and operators  --')

    t_stamp = time_count()
    print(' .. multi-patch domain...')
    
    if domain_name in ['multipatch_rectangle', 'mpr_collela']:
        if domain_name == 'multipatch_rectangle':
            F_name = 'Identity'
        else:
            F_name = 'Collela'
        
        domain, domain_h, bnds = build_multipatch_rectangle(
            nb_patch_x, nb_patch_y, 
            x_min=0, x_max=np.pi,
            y_min=0, y_max=np.pi,
            perio=[False,False],
            ncells=ncells,
            F_name=F_name,
            )

    else:
        domain = build_multipatch_domain(domain_name=domain_name)

    mappings = OrderedDict([(P.logical_domain, P.mapping) for P in domain.interior])
    mappings_list = [m.get_callable_mapping() for m in mappings.values()]

    # for diagnostics
    diag_grid = DiagGrid(mappings=mappings, N_diag=100)

    t_stamp = time_count(t_stamp)
    print('building derham sequences...')
    p_derham  = Derham(domain, ["H1", "Hcurl", "L2"])
    domain_h = discretize(domain, ncells=ncells)

    #grid_type=[np.linspace(-1,1,nc+1) for nc in ncells]
    
    x1 = 0.5
    x2 = 0.8 

    h = max([1-x2,x2-x1,x1])

    grid_type = [np.array([-1,-x2,-x1,0,x1,x2,1]) for n in ncells]

    p_derham_h = discretize(p_derham, domain_h, degree=degree, grid_type=grid_type)

    p_V0h = p_derham_h.V0
    p_V1h = p_derham_h.V1
    p_V2h = p_derham_h.V2

    if method == 'ssc':
        d_derham  = Derham(domain, ["H1", "Hdiv", "L2"])
        dual_degree = [d-1 for d in degree]
        d_derham_h = discretize(d_derham, domain_h, degree=dual_degree, grid_type=grid_type)

        d_V0h = d_derham_h.V0
        d_V1h = d_derham_h.V1
        d_V2h = d_derham_h.V2
    
    elif method == 'swc':
        d_V0h = p_derham_h.V2
        d_V1h = p_derham_h.V1
        d_V2h = p_derham_h.V0

    else:
        raise NotImplementedError
    
    t_stamp = time_count(t_stamp)
    print('building the mass matrices ...')
    
    ## NOTE: with a strong-strong diagram we should not call these "Hodge" operators !! 
    p_HOp0    = HodgeOperator(p_V0h, domain_h, backend_language=backend_language, load_dir=pm_load_dir, load_space_index=0)
    p_MM0     = p_HOp0.get_dual_Hodge_sparse_matrix()    # mass matrix
    p_MM0_inv = p_HOp0.to_sparse_matrix()                # inverse mass matrix

    p_HOp1   = HodgeOperator(p_V1h, domain_h, backend_language=backend_language, load_dir=pm_load_dir, load_space_index=1)
    p_MM1     = p_HOp1.get_dual_Hodge_sparse_matrix()    # mass matrix
    p_MM1_inv = p_HOp1.to_sparse_matrix()                # inverse mass matrix

    p_HOp2    = HodgeOperator(p_V2h, domain_h, backend_language=backend_language, load_dir=pm_load_dir, load_space_index=2)
    p_MM2     = p_HOp2.get_dual_Hodge_sparse_matrix()    # mass matrix
    p_MM2_inv = p_HOp2.to_sparse_matrix()                # inverse mass matrix


    if method == 'ssc':
        d_HOp0   = HodgeOperator(d_V0h, domain_h, backend_language=backend_language, load_dir=dm_load_dir, load_space_index=0)
        d_MM0     = d_HOp0.get_dual_Hodge_sparse_matrix()    # mass matrix
        d_MM0_inv = d_HOp0.to_sparse_matrix()                # inverse mass matrix

        d_HOp1   = HodgeOperator(d_V1h, domain_h, backend_language=backend_language, load_dir=dm_load_dir, load_space_index=1)
        d_MM1     = d_HOp1.get_dual_Hodge_sparse_matrix()    # mass matrix
        d_MM1_inv = d_HOp1.to_sparse_matrix()                # inverse mass matrix

        d_HOp2   = HodgeOperator(d_V2h, domain_h, backend_language=backend_language, load_dir=dm_load_dir, load_space_index=2)
        d_MM2    = d_HOp2.get_dual_Hodge_sparse_matrix()    # mass matrix

    elif method == 'swc':

        # not sure whether useful...
        d_MM0     = p_MM2_inv
        d_MM0_inv = p_MM2

        d_MM1     = p_MM1_inv
        d_MM1_inv = p_MM1
        
        d_MM2     = p_MM0_inv

    else:
        raise NotImplementedError


    t_stamp = time_count(t_stamp)
    print('building the conforming projection matrices ...')


    # by default we use the same C1 primal sequence in both swc and ssw -- but we should also try with C0 in swc
    p_PP0     = cps.Conf_proj_0_c1(p_V0h, nquads = [4*(d + 1) for d in degree], hom_bc=True)
    p_PP1     = cps.Conf_proj_1_c1(p_V1h, nquads = [4*(d + 1) for d in degree], hom_bc=True)

    if method == 'ssc':
        d_PP0 = cps.Conf_proj_0(d_V0h, nquads = [4*(d + 1) for d in dual_degree])
        d_PP1 = cps.Conf_proj_1(d_V1h, nquads = [4*(d + 1) for d in dual_degree])
    
    elif method == 'swc':
        d_PP0 = None
        d_PP1 = None
    
    else:
        raise NotImplementedError

    t_stamp = time_count(t_stamp)
    print('building the Hodge matrices ...')
    if method == 'ssc':
        p_KK2_storage_fn = m_load_dir+'/p_KK2.npz'
        if os.path.exists(p_KK2_storage_fn):
            # matrix is stored
            print('loading pairing matrix found in '+p_KK2_storage_fn)
            p_KK2 = load_npz(p_KK2_storage_fn)
        else:
            print('pairing matrix not found, computing... ')
            p_KK2 = construct_pairing_matrix(d_V0h,p_V2h).tocsr()  # matrix in scipy format
            t_stamp = time_count(t_stamp)
            print('storing pairing matrix in '+p_KK2_storage_fn)
            save_npz(p_KK2_storage_fn, p_KK2)

        d_KK1_storage_fn = m_load_dir+'/d_KK1.npz'
        if os.path.exists(d_KK1_storage_fn):
            # matrix is stored
            d_KK1 = load_npz(d_KK1_storage_fn)
        else:
            d_KK1 = construct_pairing_matrix(p_V1h,d_V1h).tocsr()  # matrix in scipy format
            save_npz(d_KK1_storage_fn, d_KK1)
    
        p_HH2 = d_MM0_inv @ d_PP0.transpose() @ p_KK2
        d_HH1 = p_MM1_inv @ p_PP1.transpose() @ d_KK1

    elif method == 'swc':

        p_HH2 = p_MM2 
        d_HH1 = p_MM1_inv 

    else:
        raise NotImplementedError


    t_stamp = time_count(t_stamp)
    print(' .. differential operators...')
    p_bD0, p_bD1 = p_derham_h.broken_derivatives_as_operators
    p_bG = p_bD0.to_sparse_matrix() # broken grad (primal)
    p_GG = p_bG @ p_PP0             # Conga grad (primal)
    p_bC = p_bD1.to_sparse_matrix() # broken curl (primal: scalar-valued)
    p_CC = p_bC @ p_PP1             # Conga curl (primal)

    if method == 'ssc':
        d_bD0, d_bD1 = d_derham_h.broken_derivatives_as_operators
        d_bC = d_bD0.to_sparse_matrix() # broken curl (dual: vector-valued)
        d_CC = d_bC @ d_PP0             # Conga curl (dual)    
        d_bD = d_bD1.to_sparse_matrix() # broken div
        d_DD = d_bD @ d_PP1             # Conga div (dual)    
        
    elif method == 'swc':
        d_CC = p_CC.transpose()
        d_DD = - p_GG.transpose()
        
    else:
        raise NotImplementedError


    t_stamp = time_count(t_stamp)
    print(' .. Ampere and Faraday evolution (curl . Hodge) operators...')
    Amp_Op = d_CC @ p_HH2
    Far_Op = p_CC @ d_HH1

    t_stamp = time_count(t_stamp)

    p_geomP0, p_geomP1, p_geomP2 = p_derham_h.projectors()
    if method == 'ssc':
        d_geomP0, d_geomP1, d_geomP2 = d_derham_h.projectors()
    else:
        d_geomP0 = d_geomP1 = d_geomP2 = None


    if Nt_pertau is None:
        if not( 0 < cfl <= 1):
            print(' ******  ****** ******  ****** ******  ****** ')
            print('         WARNING !!!  cfl = {}  '.format(cfl))
            print(' ******  ****** ******  ****** ******  ****** ')
        print(Amp_Op.shape)
        print(Far_Op.shape)
        print(p_V2h.nbasis)
        Nt_pertau, dt, norm_curlh = compute_stable_dt(cfl, tau, Amp_Op, Far_Op, p_V2h.nbasis)
        u, w = gauss_lobatto(ncells[0])
        #h = np.pi/(2*nb_patch_x)*(u[ncells[0]//2+1]-u[ncells[0]//2])
        #h = np.pi/(nb_patch_x*(ncells[0]-2))
        h = h*np.pi/(2*nb_patch_x)
        print(" *** with cps.proj_op = ", cps.proj_op)
        print("h    = ", h)
        print("dt   = ", dt)
        print("dt/h = ", dt/h)
        print("norm_curlh = ", norm_curlh)
        print("h*norm_curlh = ", h*norm_curlh)
        final_time = tau * nb_tau
        print('final_time = ', final_time)
        print('Nt = ', Nt_pertau * nb_tau)
        exit()
    else:
        dt = tau/Nt_pertau
        norm_curlh = None
    Nt = Nt_pertau * nb_tau

    def is_plotting_time(nt):
        answer = (nt==0) or (nt==Nt)
        for tau_range, nt_plots_pp in plot_time_ranges:
            if answer:
                break
            tp = max(Nt_pertau//nt_plots_pp,1)
            answer = (tau_range[0]*tau <= nt*dt <= tau_range[1]*tau and (nt)%tp == 0)
        return answer
    
    plot_divD = ("divD" in plot_variables)

    print(' ------ ------ ------ ------ ------ ------ ------ ------ ')
    print(' ------ ------ ------ ------ ------ ------ ------ ------ ')
    print(' total nb of time steps: Nt = {}, final time: T = {:5.4f}'.format(Nt, final_time))
    print(' ------ ------ ------ ------ ------ ------ ------ ------ ')
    print(' plotting times: the solution will be plotted for...')
    for nt in range(Nt+1):
        if is_plotting_time(nt):
            print(' * nt = {}, t = {:5.4f}'.format(nt, dt*nt))
    print(' ------ ------ ------ ------ ------ ------ ------ ------ ')
    print(' ------ ------ ------ ------ ------ ------ ------ ------ ')

    # ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- 
    # source

    t_stamp = time_count(t_stamp)
    print()
    print(' -- getting source --')
    if source_type == 'zero':
        f0_c = np.zeros(d_V1h.nbasis)

    else:
        raise ValueError(source_type)
        
    t_stamp = time_count(t_stamp)
    
    def plot_D_field(D_c, nt, project_sol=False, plot_divD=False, label=''):
        if plot_dir:

            if method == 'swc':
                Dp_c = p_MM1_inv @ D_c # get coefs in primal basis for plotting 
                Vh = p_V1h
            
            else:
                assert method == 'ssc'
                Dp_c = D_c  # keep coefs in dual space
                Vh = d_V1h

            # project the homogeneous solution on the conforming problem space
            if project_sol:
                raise NotImplementedError
                # t_stamp = time_count(t_stamp)
                print(' .. projecting the homogeneous solution on the conforming problem space...')
                Ep_c = p_PP1_m.dot(E_c)

            print(' .. plotting the '+label+' D field...')                
            title = r'$D_h$ (amplitude) at $t = {:5.4f}$'.format(dt*nt)
            
            params_str = 'Nt_pertau={}'.format(Nt_pertau)
            plot_field(numpy_coeffs=Dp_c, Vh=Vh, space_kind='hdiv', domain=domain, surface_plot=False, title=title, 
                filename=plot_dir+'/'+params_str+label+'_Dh_nt={}.pdf'.format(nt),
                plot_type='amplitude', show_grid=True, cb_min=cb_min_sol, cb_max=cb_max_sol, hide_plot=hide_plots)

            if plot_divD:
                params_str = 'Nt_pertau={}'.format(Nt_pertau)
                plot_type = 'amplitude'

                divD_c = d_DD @ Dp_c

                if method == 'swc':
                    Vh_aux = p_V0h
                    divD_c = p_MM0_inv @ divD_c # get coefs in primal basis for plotting 
                    # here, divD_c = coefs in p_V0h
                    divD_norm2 = np.dot(divD_c, p_MM0.dot(divD_c))
                else:
                    assert method == 'ssc'
                    Vh_aux = d_V2h  # plot directly in dual space                    
                    divD_norm2 = np.dot(divD_c, d_MM2.dot(divD_c))

                title = r'div $D_h$ at $t = {:5.4f}, norm = {}$'.format(dt*nt, np.sqrt(divD_norm2))
                plot_field(numpy_coeffs=divD_c, Vh=Vh_aux, space_kind='l2', domain=domain, surface_plot=False, title=title, 
                    filename=plot_dir+'/'+params_str+label+'_divDh_nt={}.pdf'.format(nt),
                    plot_type=plot_type, cb_min=None, cb_max=None, hide_plot=hide_plots)
                
        else:
            print(' -- WARNING: unknown plot_dir !!')

    def plot_B_field(B_c, nt, label=''):

        if plot_dir:

            print(' .. plotting B field...')
            params_str = 'Nt_pertau={}'.format(Nt_pertau)

            title = r'$B_h$ (amplitude) for $t = {:5.4f}$'.format(dt*nt)
            plot_field(numpy_coeffs=B_c, Vh=p_V2h, space_kind='l2', domain=domain, surface_plot=False, title=title, 
                filename=plot_dir+'/'+params_str+label+'_Bh_nt={}.pdf'.format(nt),
                plot_type='amplitude', show_grid=True, cb_min=cb_min_sol, cb_max=cb_max_sol, hide_plot=hide_plots)

        else:
            print(' -- WARNING: unknown plot_dir !!')

    def plot_time_diags(time_diag, E_norm2_diag, H_norm2_diag, divD_norm2_diag, nt_start, nt_end, 
        GaussErr_norm2_diag=None, GaussErrP_norm2_diag=None, 
        PE_norm2_diag=None, I_PE_norm2_diag=None, J_norm2_diag=None, skip_titles=True):
        nt_start = max(nt_start, 0)
        nt_end = min(nt_end, Nt)
        tau_start = nt_start/Nt_pertau
        tau_end = nt_end/Nt_pertau

        if source_is_harmonic:
            td = time_diag[nt_start:nt_end+1]/tau
            t_label = r'$t/\tau$'
        else: 
            td = time_diag[nt_start:nt_end+1]
            t_label = r'$t$'

        # norm || E ||
        fig, ax = plt.subplots()
        ax.plot(td, np.sqrt(E_norm2_diag[nt_start:nt_end+1]), '-', ms=7, mfc='None', mec='k') #, label='||E||', zorder=10)
        if skip_titles:
            title = ''
        else:
            title = r'$||E_h(t)||$ vs '+t_label
        ax.set_xlabel(t_label, fontsize=16)
        ax.set_title(title, fontsize=18)
        fig.tight_layout()
        diag_fn = plot_dir+'/diag_E_norm_Nt_pertau={}_tau_range=[{},{}].pdf'.format(Nt_pertau, tau_start, tau_end)
        print("saving plot for '"+title+"' in figure '"+diag_fn)
        fig.savefig(diag_fn)

        # energy
        fig, ax = plt.subplots()
        E_energ = .5*E_norm2_diag[nt_start:nt_end+1]
        B_energ = .5*H_norm2_diag[nt_start:nt_end+1]
        ax.plot(td, E_energ, '-', ms=7, mfc='None', c='k', label=r'$\frac{1}{2}||E||^2$') #, zorder=10)
        ax.plot(td, B_energ, '-', ms=7, mfc='None', c='g', label=r'$\frac{1}{2}||B||^2$') #, zorder=10)
        ax.plot(td, E_energ+B_energ, '-', ms=7, mfc='None', c='b', label=r'$\frac{1}{2}(||E||^2+||B||^2)$') #, zorder=10)
        ax.legend(loc='best')
        if skip_titles:  
            title = ''
        else:
            title = r'energy vs '+t_label
        if solution_type == 'pulse':
            ax.set_ylim([0, 7])
        
        ax.set_xlabel(t_label, fontsize=16)                    
        ax.set_title(title, fontsize=18)
        fig.tight_layout()
        diag_fn = plot_dir+'/diag_energy_Nt_pertau={}_tau_range=[{},{}].pdf'.format(Nt_pertau, tau_start, tau_end)
        print("saving plot for '"+title+"' in figure '"+diag_fn)
        fig.savefig(diag_fn)

        # norm || div E ||
        fig, ax = plt.subplots()
        ax.plot(td, np.sqrt(divD_norm2_diag[nt_start:nt_end+1]), '-', ms=7, mfc='None', mec='k') #, label='||E||', zorder=10)
        diag_fn = plot_dir+'/diag_divD_Nt_pertau={}_tau_range=[{},{}].pdf'.format(Nt_pertau, tau_start, tau_end)
        title = r'$||div_h E_h(t)||$ vs '+t_label 
        if skip_titles:
            title = ''
        ax.set_xlabel(t_label, fontsize=16)  
        ax.set_title(title, fontsize=18)
        fig.tight_layout()
        print("saving plot for '"+title+"' in figure '"+diag_fn)
        fig.savefig(diag_fn)
    
        if GaussErr_norm2_diag is not None:
            fig, ax = plt.subplots()            
            ax.plot(td, np.sqrt(GaussErr_norm2_diag[nt_start:nt_end+1]), '-', ms=7, mfc='None', mec='k') #, label='||E||', zorder=10)
            diag_fn = plot_dir+'/diag_GaussErr_Nt_pertau={}_tau_range=[{},{}].pdf'.format(Nt_pertau, tau_start, tau_end)
            title = r'$||(\rho_h - div_h E_h)(t)||$ vs '+t_label
            if skip_titles:
                title = ''
            ax.set_xlabel(t_label, fontsize=16)  
            ax.set_title(title, fontsize=18)
            fig.tight_layout()
            print("saving plot for '"+title+"' in figure '"+diag_fn)
            fig.savefig(diag_fn)     

        if GaussErrP_norm2_diag is not None:
            fig, ax = plt.subplots()            
            ax.plot(td, np.sqrt(GaussErrP_norm2_diag[nt_start:nt_end+1]), '-', ms=7, mfc='None', mec='k') #, label='||E||', zorder=10)
            diag_fn = plot_dir+'/diag_GaussErrP_Nt_pertau={}_tau_range=[{},{}].pdf'.format(Nt_pertau, tau_start, tau_end)
            title = r'$||(\rho_h - div_h P_h E_h)(t)||$ vs '+t_label
            if skip_titles:
                title = ''
            ax.set_xlabel(t_label, fontsize=16)  
            ax.set_title(title, fontsize=18)
            fig.tight_layout()
            print("saving plot for '"+title+"' in figure '"+diag_fn)
            fig.savefig(diag_fn)     
        
    
    def project_exact_cavity_solution(t, proj_type='P_geom'):
    
        E_ex, B_ex = get_cavity_solution(omega, kx, ky, t=t, domain=domain)

        if proj_type == 'P_geom':
            
            # E (in p_V1h) and D (in d_V1h)

            Eex_h = P_phys_hcurl(E_ex, p_geomP1, domain, mappings_list)
            Eex_c = Eex_h.coeffs.toarray()
            Bex_h = P_phys_l2(B_ex, p_geomP2, domain, mappings_list)
            Bex_c = Bex_h.coeffs.toarray()
            if method == 'swc':
                Dex_c = p_MM1 @ Eex_c
                Hex_c = p_MM2 @ Bex_c

            elif method == 'ssc':
                Dex_h = P_phys_hdiv(E_ex, d_geomP1, domain, mappings_list)
                Dex_c = Dex_h.coeffs.toarray()
                Hex_h = P_phys_h1(B_ex, d_geomP0, domain, mappings_list)
                Hex_c = Hex_h.coeffs.toarray()

            else:
                raise NotImplementedError

        elif proj_type == 'P_L2':

            tilde_Eex_c = p_derham_h.get_dual_dofs(space='V1', f=E_ex, backend_language=backend_language, return_format='numpy_array')
            Eex_c = p_MM1_inv @ tilde_Eex_c            
            tilde_B_ex_c = p_derham_h.get_dual_dofs(space='V2', f=B_ex, backend_language=backend_language, return_format='numpy_array')
            Bex_c = p_MM2_inv @ tilde_B_ex_c

            if method == 'swc':                
                Dex_c = tilde_Eex_c
                Hex_c = tilde_B_ex_c
        
            elif method == 'ssc':
                tilde_Dex_c = d_derham_h.get_dual_dofs(space='V1', f=E_ex, backend_language=backend_language, return_format='numpy_array')
                Dex_c = d_MM1_inv @ tilde_Dex_c
                tilde_H_ex_c = d_derham_h.get_dual_dofs(space='V0', f=B_ex, backend_language=backend_language, return_format='numpy_array')
                Hex_c = d_MM0_inv @ tilde_H_ex_c
        
            else:
                raise NotImplementedError
        
        else: 
            raise NotImplementedError
        
        return Dex_c, Bex_c, Eex_c, Hex_c
        
    # diags arrays
    E_energ_diag = np.zeros(Nt+1)
    H_energ_diag = np.zeros(Nt+1)
    divD_norm2_diag = np.zeros(Nt+1)
    time_diag = np.zeros(Nt+1)

    # ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- 
    # initial solution

    print(' .. initial solution ..')

    if solution_type == 'cavity':
        D_c, B_c, E_c, H_c = project_exact_cavity_solution(t=0, proj_type='P_geom')

    else:
        raise NotImplementedError    


    # ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- ----- 
    # time loop
    def compute_diags(D_c, B_c, J_c, nt):
        time_diag[nt] = (nt)*dt
        E_c = d_HH1 @ D_c
        H_c = p_HH2 @ B_c
        E_energ_diag[nt] = np.dot(E_c,p_MM1.dot(E_c))
        H_energ_diag[nt] = np.dot(H_c,d_MM0.dot(H_c))
        
        divD_c = d_DD @ D_c
        # print(divD_c.shape)
        # print(d_MM2.shape)
        # print("p_V0h.nbasis = ", p_V0h.nbasis)
        # print("p_V1h.nbasis = ", p_V1h.nbasis)
        # if method == 'swc':
        #     divD_c = p_MM0_inv @ divD_c # get coefs in primal basis for plotting 
        #     # here, divD_c = coefs in p_V0h
        #     divD_norm2 = np.dot(divD_c, p_MM0.dot(divD_c))
        # else:
        #     assert method == 'ssc'
        divD_norm2_diag[nt] = np.dot(divD_c, d_MM2.dot(divD_c))

    if "D" in plot_variables: plot_D_field(D_c, nt=0, plot_divD=plot_divD)
    if "B" in plot_variables: plot_B_field(B_c, nt=0)
    
    if solution_type == 'cavity' and ("Dex" in plot_variables or "Bex" in plot_variables):
        Dex_c, Bex_c, Eex_c, Hex_c = project_exact_cavity_solution(t=0, proj_type='P_geom')
        if "Dex" in plot_variables: plot_D_field(Dex_c, nt=0, plot_divD=plot_divD, label='_ex')
        if "Bex" in plot_variables: plot_B_field(Bex_c, nt=0, label='_ex')

    f_c = np.copy(f0_c)
    for nt in range(Nt):
        print(' .. nt+1 = {}/{}'.format(nt+1, Nt))

        # 1/2 faraday: Bn -> Bn+1/2
        B_c[:] -= (dt/2) * Far_Op @ D_c

        # ampere: En -> En+1        
        if nt == 0:
            compute_diags(D_c, B_c, f_c, nt=0)

        D_c[:] += dt * (Amp_Op @ B_c - f_c)

        # 1/2 faraday: Bn+1/2 -> Bn+1
        B_c[:] -= (dt/2) * Far_Op @ D_c

        # diags: 
        compute_diags(D_c, B_c, f_c, nt=nt+1)
        
        if is_plotting_time(nt+1):
            if "D" in plot_variables: plot_D_field(D_c, nt=nt+1, project_sol=project_sol, plot_divD=plot_divD)
            if "B" in plot_variables: plot_B_field(B_c, nt=nt+1)
            if solution_type == 'cavity' and ("Dex" in plot_variables or "Bex" in plot_variables):
                Dex_c, Bex_c, Eex_c, Hex_c = project_exact_cavity_solution(t=(nt+1)*dt, proj_type='P_geom')
                if "Dex" in plot_variables: plot_D_field(Dex_c, nt=0, plot_divD=plot_divD, label='_ex')
                if "Bex" in plot_variables: plot_B_field(Bex_c, nt=0, label='_ex')
            
        if (nt+1)%(diag_dtau*Nt_pertau) == 0:
            tau_here = nt+1
            
            plot_time_diags(
                time_diag, 
                E_energ_diag, 
                H_energ_diag, 
                divD_norm2_diag, 
                nt_start=(nt+1)-diag_dtau*Nt_pertau, 
                nt_end=(nt+1), 
            )   

    plot_time_diags(
        time_diag, 
        E_energ_diag, 
        H_energ_diag, 
        divD_norm2_diag, 
        nt_start=0, 
        nt_end=Nt, 
    )

    if solution_type == 'cavity':
        t_stamp = time_count(t_stamp)

        print(' .. comparing with a projection of the exact cavity solution...')
        Dex_c, Bex_c, Eex_c, Hex_c = project_exact_cavity_solution(t=final_time, proj_type='P_geom')

        # D error (in d_V1h)
        D_err_c = D_c - Dex_c
        D_L2_error = np.sqrt(np.dot(D_err_c, d_MM1.dot(D_err_c)))

        # E error (in p_V1h)
        # E_err_c = p_PP1 @ d_HH1 @ D_c - Eex_c
        E_err_c = d_HH1 @ D_c - Eex_c
        E_L2_error = np.sqrt(np.dot(E_err_c, p_MM1.dot(E_err_c)))

        # B error (in p_V2h)
        B_err_c = B_c - Bex_c
        B_L2_error = np.sqrt(np.dot(B_err_c, p_MM2.dot(B_err_c)))

        # H error (in d_V0h)
        # H_err_c = d_PP0 @ p_HH2 @ B_c - Hex_c
        H_err_c = p_HH2 @ B_c - Hex_c
        H_L2_error = np.sqrt(np.dot(H_err_c, d_MM0.dot(H_err_c)))
                
        print("D_error = ", D_L2_error)
        print("E_error = ", E_L2_error) 
        print("B_error = ", B_L2_error) 
        print("H_error = ", H_L2_error)

        t_stamp = time_count(t_stamp)
        diags["D_error"] = D_L2_error
        diags["E_error"] = E_L2_error
        diags["B_error"] = B_L2_error
        diags["H_error"] = H_L2_error

    return diags


def compute_stable_dt(cfl, tau, C_m, dC_m, V1_dim):

    print (" .. compute_stable_dt by estimating the operator norm of ")
    print (" ..     dC_m @ C_m: V1h -> V1h ")
    print (" ..     with dim(V1h) = {}      ...".format(V1_dim))

    def vect_norm_2 (vv):
        return np.sqrt(np.dot(vv,vv))
    t_stamp = time_count()
    vv = np.random.random(V1_dim)
    norm_vv = vect_norm_2(vv)    
    max_ncfl = 500
    ncfl = 0
    spectral_rho = 1
    conv = False
    CC_m = dC_m @ C_m
    while not( conv or ncfl > max_ncfl ):

        vv[:] = (1./norm_vv)*vv
        ncfl += 1
        vv[:] = CC_m.dot(vv)
        
        norm_vv = vect_norm_2(vv)
        old_spectral_rho = spectral_rho
        spectral_rho = norm_vv.copy() # approximation
        conv = abs((spectral_rho - old_spectral_rho)/spectral_rho) < 0.001
        print ("    ... spectral radius iteration: spectral_rho( dC_m @ C_m ) ~= {}".format(spectral_rho))
    t_stamp = time_count(t_stamp)
    
    norm_op = np.sqrt(spectral_rho)
    c_dt_max = 2./norm_op    
    
    light_c = 1
    Nt_pertau = int(np.ceil(tau/(cfl*c_dt_max/light_c)))
    assert Nt_pertau >= 1 
    dt = tau / Nt_pertau
    
    assert light_c*dt <= cfl * c_dt_max
    
    print("  Time step dt computed for Maxwell solver:")
    print("     Since cfl = " + repr(cfl)+",   we set dt = "+repr(dt)+"  --  and Nt_pertau = "+repr(Nt_pertau))
    print("     -- note that c*Dt = "+repr(light_c * dt)+", and c_dt_max = "+repr(c_dt_max)+" thus c * dt / c_dt_max = "+repr(light_c*dt/c_dt_max))
    print("     -- and spectral_radius((c*dt)**2* dC_m @ C_m ) = ",  (light_c * dt * norm_op)**2, " (should be < 4).")

    return Nt_pertau, dt, norm_op

if __name__ == '__main__':
    # quick run, to test 

    raise NotImplementedError


    t_stamp_full = time_count()

    omega = np.sqrt(170) # source
    roundoff = 1e4
    eta = int(-omega**2 * roundoff)/roundoff

    source_type = 'manu_maxwell'
    # source_type = 'manu_J'

    domain_name = 'curved_L_shape'
    nc = 4
    deg = 2

    run_dir = '{}_{}_nc={}_deg={}/'.format(domain_name, source_type, nc, deg)
    m_load_dir = 'matrices_{}_nc={}_deg={}/'.format(domain_name, nc, deg)
    solve_hcurl_source_pbm(
        nc=nc, deg=deg,
        eta=eta,
        nu=0,
        mu=1, #1,
        domain_name=domain_name,
        source_type=source_type,
        backend_language='pyccel-gcc',
        plot_source=True,
        plot_dir='./plots/tests_source_feb_13/'+run_dir,
        hide_plots=True,
        m_load_dir=m_load_dir
    )

    time_count(t_stamp_full, msg='full program')