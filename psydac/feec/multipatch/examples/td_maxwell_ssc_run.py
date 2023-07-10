from multiprocessing.sharedctypes import Value
import os
import numpy as np
from psydac.feec.multipatch.examples.td_maxwell_ssc     import solve_td_maxwell_pbm
from psydac.feec.multipatch.utilities                   import time_count, get_run_dir, get_plot_dir, get_mat_dir, get_sol_dir, diag_fn
from psydac.feec.multipatch.utils_conga_2d              import write_diags_to_file, write_errors_array_deg_nbp, write_errors_array_deg_nbc



t_stamp_full = time_count()

# ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- 
#
# main test-cases and parameters

test_case = 'cavity'   
# test_case = 'E0_pulse_no_source'   
# test_case = 'Issautier_like_source'  

# J_proj_case = 'P_geom'
# J_proj_case = 'P_L2'
# J_proj_case = 'tilde_Pi' 
J_proj_case = ''

# method:
#   - swc = strong-weak-conga
#   - ssc = strong-strong-conga
method =  'ssc' # 'swc' #

#
# ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- 

nbc_s = [6] #,8,16] #,32]
# deg_s = [4] # ,4,5]
# nbp_s = [2,4,8, 16]  # only for 'multipatch_rectangle' domain

deg_s = [3,4] # ,4,5]
nbp_s = [2,4,8,16]  # only for 'multipatch_rectangle' domain

# domain_name = 'pretzel_f'
# domain_name = 'square_9'  # for cavity solution, must be a square of diameter pi
# domain_name = 'collela_square_9'  # for cavity solution, must be a square of diameter pi
domain_name = 'multipatch_rectangle'

if test_case == 'cavity':
    a = np.pi 
    b = np.pi
    nx = 1 # 3  # mode
    ny = 2  # mode
    kx = np.pi * nx / a
    ky = np.pi * ny / b
    omega = np.sqrt(kx**2 + ky**2)
    # c = omega / np.sqrt(kx**2 + ky**2) ## = 1 here
    sol_params = {'omega':omega, 'kx':kx, 'ky':ky}
    
else:
    # c = None
    raise NotImplementedError

# tau: run / diags time scale
# may be relevant for oscillating sources but also for plotting
period_time = 2*np.pi/omega
tau = 0.01 * period_time

# must be integer ! (for now)
nb_tau = 1  # final time: T = nb_tau * tau

# plotting ranges:
#   we give a list of ranges and plotting period: [[t_start, t_end], nt_plot_period]
#   with 
#       t_start, t_end: in t_periods (tau) units
#   and 
#       nt_plots_pp: nb of plots per period
plot_time_ranges = [
    [[nb_tau-1, nb_tau], 4]
    ]

plot_variables = []

# nb of time steps per period (if None, will be set from cfl)
Nt_pertau = None
cfl = .1  

if test_case == 'E0_pulse_no_source':
    solution_type = 'pulse'
    source_type = 'zero' 
    source_is_harmonic = False
    
    nb_tau = 16 # 25 # final time: T = nb_tau * tau
    plot_a_lot = True # False # 
    if plot_a_lot:
        plot_time_ranges = [
            [[0, nb_tau], 1]
        ]
    else:
        # plot only a few snapshots
        plot_time_ranges = [
            [[0, 2], 2],
            [[nb_tau-2, nb_tau], 2],
        ]

    if domain_name == 'pretzel_f':
        if nbc_s == [20] and deg_s == [6]:
            Nt_pertau = 54  # 54 is stable for cfl = 0.8 but sometimes the solver gets 53
        if nbc_s == [8] and deg_s == [3]:
            Nt_pertau = 10

    cb_min_sol = 0
    cb_max_sol = 8 # 5  #

elif test_case == 'cavity':
    solution_type = 'cavity'
    source_type = 'zero' 
    source_is_harmonic = False

    plot_variables = ["D", "B"]
    # plot_variables = ["D", "B", "Dex", "Bex", "divD"]
    # nb_tau = 1 # 25 # final time: T = nb_tau * tau
    plot_a_lot = False # True # 
    if plot_a_lot:
        plot_time_ranges = [
            [[0, nb_tau], 10] 
        ]
    else:
        # plot only a few snapshots
        plot_time_ranges = [
            [[0, nb_tau], 1]        
        ]

    cb_min_sol = 0
    cb_max_sol = 1


elif test_case == 'Issautier_like_source':
    solution_type = 'zero'
    source_type = 'Il_pulse_pp'  # 'Il_pulse' has a coarser rho
    source_is_harmonic = False

    nb_tau = 100  #  # final time: T = nb_tau * tau
            
    if J_proj_case == 'P_geom':    
        cb_min_sol = None #
        cb_max_sol = None #
    else: 
        cb_min_sol = 0 # 
        cb_max_sol = .3 #

    if deg_s == [3] and nb_tau==100:
            
        plot_time_ranges = [
            [[9.5,10], 2],
            [[24.5,25], 2],
            [[49.5,50], 2],
            [[99.5,100], 2],
            ]

            # plot_time_ranges = [
            #     ]
            # if nbc_s == [8]:
            #     Nt_pertau = 10

else:
    raise ValueError(test_case)

# projection used for initial E0 (B0 = 0 in all cases)
solution_proj = 'P_geom' # 'P_L2' # 

# whether cP1 E_h is plotted instead of E_h:
project_sol =  False #    True # 

case_name = 'td_maxwell_' + test_case + '_' + method

# projection used for the source J
if J_proj_case == 'P_geom':
    source_proj = 'P_geom'
    filter_source =  False

elif J_proj_case == 'P_L2':
    source_proj = 'P_L2' 
    filter_source = False

elif J_proj_case == 'tilde_Pi':
    source_proj = 'P_L2' 
    filter_source =  True 

else:
    assert J_proj_case == ''
    assert test_case == 'cavity'
    source_proj = None 
    filter_source =  False

    # raise ValueError(J_proj_case)

# multiplicative parameter for the quadrature order of the bilinear/linear forms discretizations:
quad_param = 4

if J_proj_case != '':
    case_name += '_J_proj=' + J_proj_case 
case_name += '_qp{}'.format(quad_param)
if project_sol:
    case_name += '_E_proj'
case_name += '_nb_tau={}'.format(nb_tau)

# diag_dtau: tau period for intermediate diags (time curves) plotting
diag_dtau = max(1,nb_tau//10)

#
# ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- 

common_diag_dir = './'+case_name+'_'+domain_name
common_diag_filename = common_diag_dir+'/diags.txt'
if not os.path.exists(common_diag_dir):
    os.makedirs(common_diag_dir)

nb_deg = len(deg_s)
nb_nc = len(nbc_s)

E_errors = [[[ None for nbc in nbc_s] for nbp in nbp_s] for deg in deg_s]
B_errors = [[[ None for nbc in nbc_s] for nbp in nbp_s] for deg in deg_s]
H_errors = [[[ None for nbc in nbc_s] for nbp in nbp_s] for deg in deg_s]
D_errors = [[[ None for nbc in nbc_s] for nbp in nbp_s] for deg in deg_s]

for i_deg, deg in enumerate(deg_s): 
    for i_nbp, nbp in enumerate(nbp_s): 
        for i_nbc, nbc in enumerate(nbc_s): 

            params = {
                'domain_name': domain_name,
                'nb_cells': nbc,
                'deg': deg,
                'homogeneous': True,
                'solution_type': solution_type,
                'solution_proj': solution_proj,
                'source_type': source_type,
                'source_is_harmonic ': source_is_harmonic,
                'source_proj': source_proj,
                'filter_source': filter_source, 
                'project_sol': project_sol,
                'omega': omega,
                'quad_param': quad_param,
            }
            if domain_name == 'multipatch_rectangle':
                params['nbp'] = nbp

            # backend_language = 'numba'
            backend_language='python' # 'pyccel-gcc'

            # run_dir = get_run_dir(domain_name, nc, deg, source_type=source_type)
            m_load_dir = 'matrices_{}_nbp={}_nc={}_deg={}/'.format(domain_name, nbp, nbc, deg)
            run_dir = './'+case_name+'/{}_nbp={}_nc={}_deg={}/'.format(domain_name, nbp, nbc, deg)
            plot_dir= run_dir+'plots/'
            diag_filename = run_dir+'/'+diag_fn(source_type=source_type, source_proj=source_proj)

            if not os.path.exists(plot_dir):
                os.makedirs(plot_dir)

            # to save and load matrices
            # m_load_dir = get_mat_dir(domain_name, nc, deg, quad_param=quad_param)        
            # plot_dir = get_plot_dir(case_dir, run_dir)



            print('\n --- --- --- --- --- --- --- --- --- --- --- --- --- --- \n')
            print(' Calling solve_td_maxwell_pbm() with params = {}'.format(params))
            print('\n --- --- --- --- --- --- --- --- --- --- --- --- --- --- \n')
            
            # ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- 
            # calling solver for time domain maxwell
            
            diags = solve_td_maxwell_pbm(
                method=method,
                dry_run=False,
                nbc=nbc, deg=deg,
                Nt_pertau=Nt_pertau,
                cfl=cfl,
                source_is_harmonic=source_is_harmonic,
                tau=tau,
                nb_tau=nb_tau,
                sol_params=sol_params,
                domain_name=domain_name,
                nb_patch_x=nbp,
                nb_patch_y=nbp,
                solution_type=solution_type,
                solution_proj=solution_proj,
                source_type=source_type,
                source_proj=source_proj,
                backend_language=backend_language,
                quad_param=quad_param,
                plot_source=True,
                project_sol=project_sol,
                filter_source=filter_source,
                plot_dir=plot_dir,
                plot_time_ranges=plot_time_ranges,
                plot_variables=plot_variables,
                diag_dtau=diag_dtau,
                hide_plots=True,
                skip_plot_titles=True,
                cb_min_sol=cb_min_sol, 
                cb_max_sol=cb_max_sol,
                m_load_dir=m_load_dir,
            )

            #
            # ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- ---- 

            write_diags_to_file(diags, script_filename=__file__, diag_filename=diag_filename, params=params)
            write_diags_to_file(diags, script_filename=__file__, diag_filename=common_diag_filename, params=params)

            E_errors[i_deg][i_nbp][i_nbc] = diags["E_error"]
            D_errors[i_deg][i_nbp][i_nbc] = diags["D_error"]
            B_errors[i_deg][i_nbp][i_nbc] = diags["B_error"]
            H_errors[i_deg][i_nbp][i_nbc] = diags["H_error"]

print('writing error arrays for convergence curve in '+ common_diag_dir + ' ...')
    
if len(nbc_s) == 1:

    print('with increasing nb of patches (and fixed nb of cells per patch)...')
    write_errors_array_deg_nbp(E_errors, deg_s, nbp_s, nbc_s[0], error_dir=common_diag_dir, name="E")
    write_errors_array_deg_nbp(B_errors, deg_s, nbp_s, nbc_s[0], error_dir=common_diag_dir, name="B")
    write_errors_array_deg_nbp(D_errors, deg_s, nbp_s, nbc_s[0], error_dir=common_diag_dir, name="D")
    write_errors_array_deg_nbp(H_errors, deg_s, nbp_s, nbc_s[0], error_dir=common_diag_dir, name="H")

else:        

    print('with increasing nb of cells per patch (and fixed nb of patches)...')
    write_errors_array_deg_nbc(E_errors, deg_s, nbc_s, error_dir=common_diag_dir, name="E")
    write_errors_array_deg_nbc(B_errors, deg_s, nbc_s, error_dir=common_diag_dir, name="B")
    write_errors_array_deg_nbc(D_errors, deg_s, nbc_s, error_dir=common_diag_dir, name="D")
    write_errors_array_deg_nbc(H_errors, deg_s, nbc_s, error_dir=common_diag_dir, name="H")



time_count(t_stamp_full, msg='full program')