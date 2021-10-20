# script written to test Nitsche (DG) and Conga operators on multipatch domains.
# solving source problem of the form
#       A u = f
# or eigenvalue problem
#       A u = sigma u
# with
#       A u = eta * u  +  mu * curl curl u  -  nu * grad div
#
# BC's are of the form
#       n x u = g
# with g = 0 for eigenvalue problem, and g = n x E_ex for source problem
#
# Discrete spaces:
#  - Conga uses piecewise (broken) de Rham sequences V0h -> V1h -> V2H available on every space
#  - DG may use space V1h from same sequence, or another Vh (WIP)


from mpi4py import MPI

import os
import numpy as np
import matplotlib.pyplot as plt
from collections import OrderedDict

from sympy import pi, cos, sin, Matrix, Tuple, Max, exp
from sympy import symbols
from sympy import lambdify

from sympde.expr     import TerminalExpr
from sympde.calculus import grad, dot, inner, rot, div, curl, cross
from sympde.calculus import minus, plus
from sympde.topology import NormalVector
from sympde.expr     import Norm

from sympde.topology import Derham
from sympde.topology import element_of, elements_of, Domain

from sympde.topology import Square
from sympde.topology import IdentityMapping, PolarMapping
from sympde.topology import VectorFunctionSpace

from sympde.expr.equation import find, EssentialBC

from sympde.expr.expr import LinearForm, BilinearForm
from sympde.expr.expr import integral


from scipy.sparse.linalg import spsolve, spilu, cg, lgmres
from scipy.sparse.linalg import LinearOperator, eigsh, minres, gmres


from scipy.sparse.linalg import inv
from scipy.sparse.linalg import norm as spnorm
from scipy.linalg        import eig, norm
from scipy.sparse import save_npz, load_npz, bmat

# from scikits.umfpack import splu    # import error



from sympde.topology import Derham
from sympde.topology import Square
from sympde.topology import IdentityMapping, PolarMapping

from psydac.feec.multipatch.api import discretize
from psydac.feec.pull_push     import pull_2d_h1, pull_2d_hcurl

from psydac.linalg.utilities import array_to_stencil

from psydac.fem.basic   import FemField

from psydac.api.settings        import PSYDAC_BACKENDS
from psydac.feec.multipatch.fem_linear_operators import FemLinearOperator, IdLinearOperator
from psydac.feec.multipatch.fem_linear_operators import SumLinearOperator, MultLinearOperator, ComposedLinearOperator
from psydac.feec.multipatch.operators import BrokenMass, get_K0_and_K0_inv, get_K1_and_K1_inv, get_M_and_M_inv
from psydac.feec.multipatch.operators import ConformingProjection_V0, ConformingProjection_V1, time_count
from psydac.feec.multipatch.plotting_utilities import get_grid_vals_scalar, get_grid_vals_vector, get_grid_quad_weights
from psydac.feec.multipatch.plotting_utilities import get_plotting_grid, my_small_plot, my_small_streamplot
from psydac.feec.multipatch.multipatch_domain_utilities import build_multipatch_domain, get_ref_eigenvalues

comm = MPI.COMM_WORLD

# ---------------------------------------------------------------------------------------------------------------
# small utility for saving/loading sparse matrices, plots...
def rhs_fn(source_type, nbc=False, eta=None, mu=None, nu=None, dc_pbm=False, c_grad=1, npz_suffix=True, prefix=True, Psource='L2s'):
    if prefix:
        fn = 'rhs_'
    else:
        fn = ''
    fn += source_type
    if dc_pbm:
        fn += '_dc_pbm'
        if source_type == 'ellip_J':
            fn += '_c_grad'+repr(c_grad)
    if Psource == 'P1s':
        fn += '_P1s'
    if source_type == 'manu_J':
        assert (eta is not None) and (mu is not None) and (nu is not None)
        fn += '_eta'+repr(eta)+'_mu'+repr(mu)+'_nu'+repr(nu)
    if nbc:
        # additional terms for nitsche bc
        fn += '_nbc'
    if npz_suffix:
        fn += '.npz'
    return fn

def E_ref_fn(source_type, N_diag, Psource='L2s', dc_pbm=False):
    fn = 'E_ref_'+source_type+'_N'+repr(N_diag)
    if dc_pbm:
        fn += '_dc_pbm'
    if Psource == 'P1s':
        fn += '_P1s'
    elif Psource == 'L2s':
        # default projection (L2)
        pass
    else:
        raise ValueError(Psource)

    fn += '.npz'
    return fn

def hf_fn():  # domain_name):
    fn = 'hf.npz'
    return fn

def Eh_coeffs_fn(source_type, N_diag):
    return 'Eh_coeffs_'+source_type+'_N'+repr(N_diag)+'.npz'

def error_fn(source_type=None, method=None, k=None, domain_name=None,deg=None):
    return 'errors/error_'+domain_name+'_'+source_type+'_'+'_deg'+repr(deg)+'_'+get_method_name(method, k)+'.txt'

def get_method_name(method=None, k=None, geo_cproj=None, penal_regime=None):
    if method == 'nitsche':
        method_name = method
        if k==1:
            method_name += '_SIP'
        elif k==-1:
            method_name += '_NIP'
        elif k==0:
            method_name += '_IIP'
        else:
            assert k is None
    elif method == 'conga':
        method_name = method
        if geo_cproj is not None:
            if geo_cproj:
                method_name += '_GSP'  # Geometric-Spline-Projection
            else:
                method_name += '_BSP'  # B-Spline-Projection
    else:
        raise ValueError(method)
    if penal_regime is not None:
        method_name += '_pr'+repr(penal_regime)

    return method_name

def get_fem_name(method=None, k=None, DG_full=False, geo_cproj=None, domain_name=None,nc=None,deg=None):
    assert domain_name and nc and deg
    fn = domain_name+'_nc'+repr(nc)+'_deg'+repr(deg)
    if DG_full:
        fn += '_fDG'
    if method is not None:
        fn += '_'+get_method_name(method, k, geo_cproj)
    return fn

def get_load_dir(method=None, DG_full=False, domain_name=None,nc=None,deg=None,data='matrices'):
    assert data in ['matrices','solutions','rhs']
    if method is None:
        assert data == 'rhs'
    fem_name = get_fem_name(domain_name=domain_name,method=method, nc=nc,deg=deg, DG_full=DG_full)
    return './saved_'+data+'/'+fem_name+'/'



# ---------------------------------------------------------------------------------------------------------------
def get_elementary_conga_matrices(domain_h, derham_h, load_dir=None, backend_language='python', discard_non_hom_matrices=False):

    if os.path.exists(load_dir):
        print(" -- load directory " + load_dir + " found -- will load the CONGA matrices from there...")

        # print("loading sparse matrices...")
        M0_m = load_npz(load_dir+'M0_m.npz')
        M1_m = load_npz(load_dir+'M1_m.npz')
        M2_m = load_npz(load_dir+'M2_m.npz')
        M0_minv = load_npz(load_dir+'M0_minv.npz')
        cP0_m = load_npz(load_dir+'cP0_m.npz')
        cP1_m = load_npz(load_dir+'cP1_m.npz')
        cP0_hom_m = load_npz(load_dir+'cP0_hom_m.npz')
        cP1_hom_m = load_npz(load_dir+'cP1_hom_m.npz')
        bD0_m = load_npz(load_dir+'bD0_m.npz')
        bD1_m = load_npz(load_dir+'bD1_m.npz')
        I1_m = load_npz(load_dir+'I1_m.npz')

        # print('loaded.')
    else:
        print(" -- load directory " + load_dir + " not found -- will assemble the CONGA matrices...")

        V0h = derham_h.V0
        V1h = derham_h.V1
        V2h = derham_h.V2

        # Mass matrices for broken spaces (block-diagonal)
        t_stamp = time_count()
        print("assembling mass matrix operators...")

        M0 = BrokenMass(V0h, domain_h, is_scalar=True, backend_language=backend_language)
        M1 = BrokenMass(V1h, domain_h, is_scalar=False, backend_language=backend_language)
        M2 = BrokenMass(V2h, domain_h, is_scalar=True, backend_language=backend_language)

        t_stamp = time_count(t_stamp)
        print('----------     inv M0')
        M0_minv = M0.get_sparse_inverse_matrix()

        t_stamp = time_count(t_stamp)
        print("assembling conf projection operators for V1...")
        cP1_hom = ConformingProjection_V1(V1h, domain_h, hom_bc=True, backend_language=backend_language)
        t_stamp = time_count(t_stamp)
        print("assembling conf projection operators for V0...")
        cP0_hom = ConformingProjection_V0(V0h, domain_h, hom_bc=True, backend_language=backend_language)
        t_stamp = time_count(t_stamp)
        if discard_non_hom_matrices:
            print(' -- WARNING: for homogeneous bc, we discard the non-homogeneous cP0 and cP1 projection operators -- ')
            cP0 = cP0_hom
            cP1 = cP1_hom
        else:
            cP0 = ConformingProjection_V0(V0h, domain_h, hom_bc=False, backend_language=backend_language)
            cP1 = ConformingProjection_V1(V1h, domain_h, hom_bc=False, backend_language=backend_language)

        t_stamp = time_count(t_stamp)
        print("assembling broken derivative operators...")
        bD0, bD1 = derham_h.broken_derivatives_as_operators

        # t_stamp = time_count(t_stamp)
        # print("assembling conga derivative operators...")

        # D0 = ComposedLinearOperator([bD0,cP0])
        # D1 = ComposedLinearOperator([bD1,cP1])
        I1 = IdLinearOperator(V1h)

        t_stamp = time_count(t_stamp)
        print("converting in sparse matrices...")
        M0_m = M0.to_sparse_matrix()
        M1_m = M1.to_sparse_matrix()
        M2_m = M2.to_sparse_matrix()
        cP0_m = cP0.to_sparse_matrix()
        cP1_m = cP1.to_sparse_matrix()
        cP0_hom_m = cP0_hom.to_sparse_matrix()
        cP1_hom_m = cP1_hom.to_sparse_matrix()
        bD0_m = bD0.to_sparse_matrix()  # broken (patch-local) differential
        bD1_m = bD1.to_sparse_matrix()
        I1_m = I1.to_sparse_matrix()
        t_stamp = time_count(t_stamp)


        print(" -- now saving these matrices in " + load_dir)
        os.makedirs(load_dir)

        t_stamp = time_count(t_stamp)
        save_npz(load_dir+'M0_m.npz', M0_m)
        save_npz(load_dir+'M1_m.npz', M1_m)
        save_npz(load_dir+'M2_m.npz', M2_m)
        save_npz(load_dir+'M0_minv.npz', M0_minv)
        save_npz(load_dir+'cP0_m.npz', cP0_m)
        save_npz(load_dir+'cP1_m.npz', cP1_m)
        save_npz(load_dir+'cP0_hom_m.npz', cP0_hom_m)
        save_npz(load_dir+'cP1_hom_m.npz', cP1_hom_m)
        save_npz(load_dir+'bD0_m.npz', bD0_m)
        save_npz(load_dir+'bD1_m.npz', bD1_m)
        save_npz(load_dir+'I1_m.npz', I1_m)
        time_count(t_stamp)

    print('ok, got the matrices. Some shapes are: \n M0_m = {0}\n M1_m = {1}\n M2_m = {2}'.format(M0_m.shape,M1_m.shape,M2_m.shape))

    V0h = derham_h.V0
    K0, K0_inv = get_K0_and_K0_inv(V0h, uniform_patches=True)
    V1h = derham_h.V1
    K1, K1_inv = get_K1_and_K1_inv(V1h, uniform_patches=True)

    print('  -- some more shapes: \n K0 = {0}\n K1_inv = {1}\n'.format(K0.shape,K1_inv.shape))

    M_mats = [M0_m, M1_m, M2_m, M0_minv]
    P_mats = [cP0_m, cP1_m, cP0_hom_m, cP1_hom_m]
    D_mats = [bD0_m, bD1_m]
    IK_mats = [I1_m, K0, K0_inv, K1, K1_inv]

    return M_mats, P_mats, D_mats, IK_mats


def conga_operators_2d(M1_m=None, M2_m=None, cP1_m=None, cP1_hom_m=None, bD1_m=None, I1_m=None, hom_bc=True, need_GD_matrix=False):
    """
    computes
        CC_m: the (unpenalized) CONGA (stiffness) matrix of the curl-curl operator in V1, with homogeneous bc
        CC_bc_m: similar matrix for the lifted BC
        GD_m: stiffness matrix for grad div operator
        GD_bc_m: grad div operator for lifted BC (does not work / todo: improve)
        JP_m: the jump penalization matrix

    :return: matrices in sparse format
    """

    if not hom_bc:
        assert cP1_m is not None
    print('computing Conga curl_curl matrix with penalization gamma_h = {}'.format(gamma_h))
    t_stamp = time_count()

    # curl_curl matrix (stiffness, i.e. left-multiplied by M1_m) :
    D1_hom_m = bD1_m @ cP1_hom_m
    CC_m = D1_hom_m.transpose() @ M2_m @ D1_hom_m

    if need_GD_matrix:
        print('computing also Conga grad-div matrix...')
        # grad_div matrix (stiffness, i.e. left-multiplied by M1_m) :
        # D0_hom_m = bD0_m @ cP0_hom_m   # matrix of conga gradient
        # div_aux_m = D0_hom_m.transpose() @ M1_m  # the matrix of the (weak) div operator is  - M0_minv * div_aux_m
        # GD_m = - div_aux_m.transpose() * M0_minv * div_aux_m
        pre_GD_m = - M1_m @ bD0_m @ cP0_hom_m @ M0_minv @ cP0_hom_m.transpose() @ bD0_m.transpose() @ M1_m
        GD_m = cP1_hom_m.transpose() @ pre_GD_m @ cP1_hom_m
    else:
        GD_m = None

    if not hom_bc:
        # then we also need the matrix of the non-homogeneous operator
        D1_m = bD1_m * cP1_m
        CC_bc_m = D1_hom_m.transpose() * M2_m * D1_m
        if need_GD_matrix:
            GD_bc_m = cP1_hom_m.transpose() @ pre_GD_m @ cP1_m
        else:
            GD_bc_m = None
    else:
        CC_bc_m = None
        GD_bc_m = None

    # jump penalization
    jump_penal_hom_m = I1_m-cP1_hom_m
    JP_m = jump_penal_hom_m.transpose() * M1_m * jump_penal_hom_m
    time_count(t_stamp)

    return CC_m, CC_bc_m, GD_m, GD_bc_m, JP_m

# ---------------------------------------------------------------------------------------------------------------
def nitsche_operators_2d(domain_h, Vh, Qh=None, k=None, load_dir=None, backend_language='python',
                         need_D_matrix=False,
                         need_JPQ_matrix=False,
                         need_GD_matrix=False,
                         need_mass_matrix=False):
    """
    computes
        CC_m the k-IP (stiffness) matrix of the curl-curl operator
        GD_m the k-IP (stiffness) matrix of the grad-div operator, if needed  [[ not verified -- and not allowed by SymPDE yet ]]
        D_m the k-IP (stiffness) matrix of the div operator : V -> Q, if needed
        JPQ_m the jump penalization matrix in Q (H1 scalar space), if needed
        JP_m the jump penalization matrix
        M_m the mass matrix (if needed)

    Ref for the penalized curl-curl matrix: Buffa, Houston & Perugia, JCAM 2007

    :param k: parameter for SIP/NIP/IIP
    :return: matrices in sparse format
    """
    M_m = None
    got_mass_matrix = (not need_mass_matrix)
    GD_m = None
    got_GD_matrices = (not need_GD_matrix)
    D_m = None
    got_D_matrix = (not need_D_matrix)
    JPQ_m = None
    got_JPQ_matrix = (not need_JPQ_matrix)

    if os.path.exists(load_dir):
        print(" -- load directory " + load_dir + " found -- will load the Nitsche matrices from there...")

        # unpenalized curl-curl matrix (main part and symmetrization term)
        IC_m = load_npz(load_dir+'ICC_m.npz')
        CCS_m = load_npz(load_dir+'CCS_m.npz')

        # unpenalized grad-div matrix (main part and symmetrization term)
        if need_GD_matrix:
            try:
                IGD_m = load_npz(load_dir+'IGD_m.npz')
                GDS_m = load_npz(load_dir+'GDS_m.npz')
                got_GD_matrices = True
            except:
                print(" -- (IGD and GDS matrices not found)")

        # unpenalized div matrix
        if need_D_matrix:
            try:
                D_m = load_npz(load_dir+'D_m.npz')
                got_D_matrix = True
            except:
                print(" -- (D matrix not found)")

        # jump penalization matrix (for Hcurl discretization)
        JP_m = load_npz(load_dir+'JP_m.npz')

        # JPQ matrix (for H1 scalar space)
        if need_JPQ_matrix:
            try:
                JPQ_m = load_npz(load_dir+'JPQ_m.npz')
                got_JPQ_matrix = True
            except:
                print(" -- (JPQ matrix not found)")

        # mass matrix
        if need_mass_matrix:
            try:
                M_m = load_npz(load_dir+'M_m.npz')
                got_mass_matrix = True
            except:
                print(" -- (mass matrix not found)")

    else:
        print(" -- load directory " + load_dir + " not found -- will assemble the Nitsche matrices...")

        t_stamp = time_count()
        print('computing IP curl-curl matrix with k = {0}'.format(k))

        #+++++++++++++++++++++++++++++++
        # Abstract IP model
        #+++++++++++++++++++++++++++++++

        V = Vh.symbolic_space
        domain = V.domain

        u, v  = elements_of(V, names='u, v')
        nn  = NormalVector('nn')

        I        = domain.interfaces
        boundary = domain.boundary

        jump = lambda w:plus(w)-minus(w)
        avr_curl = lambda w:(curl(plus(w)) + curl(minus(w)))/2
        # avr_div = lambda w:(div(plus(w)) + div(minus(w)))/2

        # Bilinear forms a: V x V --> R

        # note (MCP): IP formulations involve tangential jumps [v]_T = n^- x v^- + n^+ x v^+
        # and normal jump  [v]_N = n^- . v^- + n^+ . v^+
        # here nn = n^- and jump(v) = v^+ - v^-
        # so that
        # [v]_T = -cross(nn, jump(v))
        # [v]_N = -dot(nn,jump(v))

        # curl-curl bilinear form
        expr_cc    =  curl(u)*curl(v)
        expr_cc_I  =  cross(nn, jump(v))*avr_curl(u)
        expr_cc_b  = -cross(nn, v      )*curl(u)

        expr_cc_Is =  cross(nn, jump(u))*avr_curl(v)   # symmetrization terms
        expr_cc_bs = -cross(nn, u      )*curl(v)

        a_cc = BilinearForm((u,v),  integral(domain, expr_cc) + integral(I, expr_cc_I) + integral(boundary, expr_cc_b))
        a_cc_s = BilinearForm((u,v),  integral(I, expr_cc_Is) + integral(boundary, expr_cc_bs))  # symmetrization terms

        # tangential jump penalization
        expr_jp_I = cross(nn, jump(u))*cross(nn, jump(v))
        expr_jp_b = cross(nn, u)*cross(nn, v)
        a_jp = BilinearForm((u,v),  integral(I, expr_jp_I) + integral(boundary, expr_jp_b))

        #+++++++++++++++++++++++++++++++
        # 2. Discretization
        #+++++++++++++++++++++++++++++++

        # domain_h = discretize(domain, ncells=ncells, comm=comm)
        # Vh       = discretize(V, domain_h, degree=degree,basis='M')

        # incomplete curl-curl matrix
        a_h = discretize(a_cc, domain_h, [Vh, Vh], backend=PSYDAC_BACKENDS[backend_language])
        A = a_h.assemble()
        ICC_m  = A.tosparse().tocsr()

        # symmetrization part (for SIP or NIP curl-curl matrix)
        a_h = discretize(a_cc_s, domain_h, [Vh, Vh], backend=PSYDAC_BACKENDS[backend_language])
        A = a_h.assemble()
        CCS_m  = A.tosparse().tocsr()

        # jump penalization matrix
        a_h = discretize(a_jp, domain_h, [Vh, Vh], backend=PSYDAC_BACKENDS[backend_language])
        A = a_h.assemble()
        JP_m  = A.tosparse().tocsr()

        print(" -- now saving these matrices in " + load_dir + "...")
        os.makedirs(load_dir)
        t_stamp = time_count(t_stamp)
        save_npz(load_dir+'ICC_m.npz', ICC_m)
        save_npz(load_dir+'CCS_m.npz', CCS_m)
        save_npz(load_dir+'JP_m.npz', JP_m)
        time_count(t_stamp)

    if not got_GD_matrices:

        #+++++++++++++++++++++++++++++++
        # Abstract IP model
        #+++++++++++++++++++++++++++++++

        V = Vh.symbolic_space
        domain = V.domain

        u, v  = elements_of(V, names='u, v')
        nn  = NormalVector('nn')

        I        = domain.interfaces
        boundary = domain.boundary

        jump = lambda w:plus(w)-minus(w)
        avr_div = lambda w:(div(plus(w)) + div(minus(w)))/2

        # grad-div bilinear form
        expr_gd   = -div(u)*div(v)
        expr_gd_I = -dot(nn, jump(v))*avr_div(u)
        expr_gd_b =  dot(nn, v      )*div(u)

        expr_gd_Is = -dot(nn, jump(u))*avr_div(v)   # symmetrization terms
        expr_gd_bs =  dot(nn, u      )*div(v)

        a_gd  = BilinearForm((u,v),  integral(domain, expr_gd) + integral(I, expr_gd_I) + integral(boundary, expr_gd_b))
        a_gd_s = BilinearForm((u,v),  integral(I, expr_gd_Is) + integral(boundary, expr_gd_bs))  # symmetrization terms

        # incomplete grad-div matrix
        a_h = discretize(a_gd, domain_h, [Vh, Vh], backend=PSYDAC_BACKENDS[backend_language])
        A = a_h.assemble()
        IGD_m  = A.tosparse().tocsr()

        # symmetrization part (for SIP or NIP grad-div matrix)
        a_h = discretize(a_gd_s, domain_h, [Vh, Vh], backend=PSYDAC_BACKENDS[backend_language])
        A = a_h.assemble()
        GDS_m  = A.tosparse().tocsr()

        print(" -- now saving these matrices in " + load_dir + "...")
        os.makedirs(load_dir)
        t_stamp = time_count(t_stamp)
        save_npz(load_dir+'IGD_m.npz', IGD_m)
        save_npz(load_dir+'GDS_m.npz', GDS_m)
        time_count(t_stamp)

    if not got_D_matrix:

        #+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
        # IP model for < div v, q >  bilinear form on V x Q
        # with stiffness matrix of shape dim_V x dim_Q
        #+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

        V = Vh.symbolic_space
        Q = Qh.symbolic_space
        domain = V.domain

        v  = element_of(V, name='v')
        q  = element_of(Q, name='q')
        nn  = NormalVector('nn')

        I        = domain.interfaces
        boundary = domain.boundary

        jump = lambda w:plus(w)-minus(w)
        avr = lambda w:(plus(w) + minus(w))/2

        expr_d   = -dot(v,grad(q))
        expr_d_I = -dot(nn, avr(v))*jump(q)   # nn is n- so (q-n- + q+n+) is -nn*jump(q)
        expr_d_b =  dot(nn,     v )*q

        a_d  = BilinearForm((v,q),  integral(domain, expr_d) + integral(I, expr_d_I) + integral(boundary, expr_d_b))
        # a_d  = BilinearForm((v,q),  integral(domain, expr_d)) # test
        # no symmetrization terms here

        # div matrix
        a_h = discretize(a_d, domain_h, [Vh, Qh], backend=PSYDAC_BACKENDS[backend_language])
        A = a_h.assemble()
        D_m  = A.tosparse().tocsr()

        print(" -- now saving this matrix in " + load_dir + "...")
        os.makedirs(load_dir)
        t_stamp = time_count(t_stamp)
        save_npz(load_dir+'D_m.npz', G_m)
        time_count(t_stamp)

    if not got_JPQ_matrix:

        #+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
        # IP matrix on Q (H1 scalar space)
        #+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

        Q = Qh.symbolic_space
        domain = Q.domain

        p,q  = elements_of(Q, names='p,q')

        I        = domain.interfaces
        boundary = domain.boundary

        jump = lambda w:plus(w)-minus(w)

        # jump penalization
        expr_jp_I = jump(p)*jump(q)
        expr_jp_b = p*q
        a_jp = BilinearForm((p,q),  integral(I, expr_jp_I) + integral(boundary, expr_jp_b))

        # jump penalization matrix
        a_h = discretize(a_jp, domain_h, [Qh, Qh], backend=PSYDAC_BACKENDS[backend_language])
        A = a_h.assemble()
        JPQ_m  = A.tosparse().tocsr()

        print(" -- now saving this matrix in " + load_dir + "...")
        os.makedirs(load_dir)
        t_stamp = time_count(t_stamp)
        save_npz(load_dir+'JPQ_m.npz', JPQ_m)
        time_count(t_stamp)

    if not got_mass_matrix:
        print(" -- assembling the mass matrix (and saving to file)...")
        V = Vh.symbolic_space
        domain = V.domain
        u, v  = elements_of(V, names='u, v')
        expr   = dot(u,v)
        a_m  = BilinearForm((u,v),  integral(domain, expr))
        m_h = discretize(a_m, domain_h, [Vh, Vh], backend=PSYDAC_BACKENDS[backend_language])
        M = m_h.assemble()
        M_m  = M.tosparse().tocsr()
        save_npz(load_dir+'M_m.npz', M_m)

    CC_m = ICC_m + k*CCS_m
    # K_m = CC_m + k*CS_m + gamma_h*JP_m
    # raise NotImplementedError
    GD_m = IGD_m + k*GDS_m

    return CC_m, GD_m, D_m, JP_m, JPQ_m, M_m

# ---------------------------------------------------------------------------------------------------------------

def get_eigenvalues(nb_eigs, sigma, A_m, M_m):
    print('-----  -----  -----  -----  -----  -----  -----  -----  -----  -----  -----  -----  -----  -----  -----  ----- ')
    print('computing {0} eigenvalues (and eigenvectors) close to sigma={1} with scipy.sparse.eigsh...'.format(nb_eigs, sigma) )

    if sigma == 0:
        # computing kernel
        mode = 'normal'
        which = 'LM'
    else:
        # ahah
        mode = 'normal'
        # mode='cayley'
        # mode='buckling'
        which = 'LM'

    # from eigsh docstring:
    #   ncv = number of Lanczos vectors generated ncv must be greater than k and smaller than n;
    #   it is recommended that ncv > 2*k. Default: min(n, max(2*k + 1, 20))
    ncv = 4*nb_eigs
    # search mode: normal and buckling give a lot of zero eigenmodes. Cayley seems best for Maxwell.
    # mode='normal'

    t_stamp = time_count()
    print('A_m.shape = ', A_m.shape)
    # print('getting sigma = ', sigma)
    # sigma_ref = ref_sigmas[len(ref_sigmas)//2] if nitsche else 0
    if A_m.shape[0] < 20000: #17000:   # max value for super_lu is >= 13200
        print('(with super_lu decomposition)')
        eigenvalues, eigenvectors = eigsh(A_m, k=nb_eigs, M=M_m, sigma=sigma, mode=mode, which=which, ncv=ncv)
    else:
        # from https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.linalg.eigsh.html:
        # the user can supply the matrix or operator OPinv, which gives x = OPinv @ b = [A - sigma * M]^-1 @ b.
        # > here, minres: MINimum RESidual iteration to solve Ax=b
        # suggested in https://github.com/scipy/scipy/issues/4170
        OP = A_m - sigma*M_m
        print('(with minres iterative solver for A_m - sigma*M1_m)')
        OPinv = LinearOperator(matvec=lambda v: minres(OP, v, tol=1e-10)[0], shape=M_m.shape, dtype=M_m.dtype)
        # print('(with gmres iterative solver for A_m - sigma*M1_m)')
        # OPinv = LinearOperator(matvec=lambda v: gmres(OP, v, tol=1e-7)[0], shape=M1_m.shape, dtype=M1_m.dtype)
        # print('(with spsolve solver for A_m - sigma*M1_m)')
        # OPinv = LinearOperator(matvec=lambda v: spsolve(OP, v, use_umfpack=True), shape=M1_m.shape, dtype=M1_m.dtype)

        # lu = splu(OP)
        # OPinv = LinearOperator(matvec=lambda v: lu.solve(v), shape=M1_m.shape, dtype=M1_m.dtype)
        eigenvalues, eigenvectors = eigsh(A_m, k=nb_eigs, M=M_m, sigma=sigma, mode=mode, which=which, ncv=ncv, tol=1e-10, OPinv=OPinv)

    time_count(t_stamp)
    print("done. eigenvalues found: " + repr(eigenvalues))
    return eigenvalues, eigenvectors


def get_source_and_solution(source_type, eta, mu, nu, domain, refsol_params=None, dc_pbm=False, c_grad=1):
    """
    get source and ref solution of time-Harmonic Maxwell equation
        eta * E + mu * curl curl E - nu * grad div E = f
    with u in H_0(curl)

    if dc_pbm, we solve a divergence-constrained mixed problem of the form
        grad p + mu * curl curl u = f
        div u = 0
    with p in H^1_0, u in H_0(curl)
    """

    assert refsol_params
    nc_ref, deg_ref, N_diag, method_ref, Psource_ref, dc_bpm_ref = refsol_params

    # ref solution (values on diag grid)
    E_ref_vals = None

    # bc solution: describe the bc on boundary. Inside domain, values should not matter. Homogeneous bc will be used if None
    E_bc = None

    E_ex = None
    p_ex = None  # for dc_pbm

    grad_phi = None # debug dc_pbm
    phi = None  # debug dc_pbm

    x,y    = domain.coordinates

    if source_type == 'manu_J':
        # use a manufactured solution, with ad-hoc (homogeneous or inhomogeneous) bc
        if domain_name in ['square_2', 'square_6', 'square_8', 'square_9']:
            t = 1
        else:
            t = pi

        if dc_pbm:
            c = 2 # parameter

            E_ex = Tuple(sin(t*y)*cos(t*x), -sin(t*x)*cos(t*y))
            p_ex = sin(c*t*x) * sin(c*t*y)
            f    = Tuple(
                c*t*cos(c*t*x)*sin(c*t*y) + mu*2*t**2 * ( sin(t*y)*cos(t*x)),
                c*t*sin(c*t*x)*cos(c*t*y) + mu*2*t**2 * (-sin(t*x)*cos(t*y))
            )

        else:
            E_ex   = Tuple(sin(t*y), sin(t*x)*cos(t*y))
            f      = Tuple(
                sin(t*y) * (eta + t**2 *(mu - cos(t*x)*(mu-nu))),
                sin(t*x) * cos(t*y) * (eta + t**2 *(mu+nu) )
            )

        E_ex_x = lambdify(domain.coordinates, E_ex[0])
        E_ex_y = lambdify(domain.coordinates, E_ex[1])
        E_ex_log = [pull_2d_hcurl([E_ex_x,E_ex_y], f) for f in mappings_list]
        E_ref_x_vals, E_ref_y_vals   = grid_vals_hcurl_cdiag(E_ex_log)
        E_ref_vals = [E_ref_x_vals, E_ref_y_vals]
        # print(E_ex_x)

        # boundary condition: (here we only need to coincide with E_ex on the boundary !)
        if domain_name in ['square_2', 'square_6', 'square_9']:
            E_bc = None
        else:
            E_bc = E_ex

    elif source_type == 'df_J':
        # div-free J
        f = Tuple(10*sin(y), -10*sin(x))

    elif source_type == 'cf_J':
        # curl-free J
        f = Tuple(10*sin(x), -10*sin(y))

    elif source_type == 'dipcurl_J':
        # here, f will be the curl of a dipole + phi_0 - phi_1 (two blobs) that correspond to a scalar current density
        # the solution of the curl-curl problem with free-divergence constraint
        #   curl curl u = curl j
        #
        # then corresponds to a magnetic density,
        # see Beirão da Veiga, Brezzi, Dassi, Marini and Russo, Virtual Element approx of 2D magnetostatic pbms, CMAME 327 (2017)

        x_0 = 2.0
        y_0 = 2.0
        ds2_0 = (0.02)**2
        sigma_0 = (x-x_0)**2 + (y-y_0)**2
        phi_0 = exp(-sigma_0**2/(2*ds2_0))
        dx_sig_0 = 2*(x-x_0)
        dy_sig_0 = 2*(y-y_0)
        dx_phi_0 = - dx_sig_0 * sigma_0 / ds2_0 * phi_0
        dy_phi_0 = - dy_sig_0 * sigma_0 / ds2_0 * phi_0

        x_1 = 1.0
        y_1 = 1.0
        ds2_1 = (0.02)**2
        sigma_1 = (x-x_1)**2 + (y-y_1)**2
        phi_1 = exp(-sigma_1**2/(2*ds2_1))
        dx_sig_1 = 2*(x-x_1)
        dy_sig_1 = 2*(y-y_1)
        dx_phi_1 = - dx_sig_1 * sigma_1 / ds2_1 * phi_1
        dy_phi_1 = - dy_sig_1 * sigma_1 / ds2_1 * phi_1

        f_x =   dy_phi_0 - dy_phi_1
        f_y = - dx_phi_0 + dx_phi_1
        f = Tuple(f_x, f_y)  # todo: rename the J's as f, throughout doc


    elif source_type == 'ellip_J':

        # divergence-free J field along an ellipse curve
        if domain_name in ['pretzel', 'pretzel_f']:
            # J_factor = 10
            dr = 0.2
            r0 = 1
            x0 = 1.5
            y0 = 1.5
            s0 = x0-y0
            t0 = x0+y0
            s  = x - y
            t  = x + y
            a2 = (1/1.7)**2
            b2 = (1/1.1)**2
            dsigpsi2 = 0.01
            sigma = a2*(s-s0)**2 + b2*(t-t0)**2 - 1
            psi = exp(-sigma**2/(2*dsigpsi2))
            dx_sig = 2*( a2*(s-s0) + b2*(t-t0))
            dy_sig = 2*(-a2*(s-s0) + b2*(t-t0))
            J_x =   dy_sig * psi
            J_y = - dx_sig * psi

            dsigphi2 = 0.01     # this one gives approx 1e-10 at boundary for phi
            # dsigphi2 = 0.005   # if needed: smaller support for phi, to have a smaller value at boundary
            phi = exp(-sigma**2/(2*dsigphi2))
            dx_phi = phi*(-dx_sig*sigma/dsigphi2)
            dy_phi = phi*(-dy_sig*sigma/dsigphi2)

            if dc_pbm:
                print('adding c_grad * grad phi in J, with c_grad = {}'.format(c_grad))
                # J just above is div-free, for the divergence-constrained problem we add a gradient term (with hom. bc) in the source
                J_x += c_grad * dx_phi
                J_y += c_grad * dy_phi
                # grad_phi = Tuple(dx_phi, dy_phi)

            grad_phi = Tuple(dx_phi, dy_phi)
            f = Tuple(J_x, J_y)  # J

        else:
            raise NotImplementedError

    elif source_type in ['ring_J', 'sring_J']:

        # 'rotating' (divergence-free) J field:

        if domain_name in ['square_2', 'square_6', 'square_8', 'square_9']:
            r0 = np.pi/4
            dr = 0.1
            x0 = np.pi/2
            y0 = np.pi/2
            omega = 43/2
            # alpha  = -omega**2  # not a square eigenvalue
            J_factor = 100

        elif domain_name in ['curved_L_shape']:
            r0 = np.pi/4
            dr = 0.1
            x0 = np.pi/2
            y0 = np.pi/2
            omega = 43/2
            # alpha  = -omega**2  # not a square eigenvalue
            J_factor = 100

        else:
            # for pretzel

            # omega = 8  # ?
            # alpha  = -omega**2

            source_option = 2

            if source_option==1:
                # big circle:
                r0 = 2.4
                dr = 0.05
                x0 = 0
                y0 = 0.5
                J_factor = 10

            elif source_option==2:
                # small circle in corner:
                if source_type == 'ring_J':
                    dr = 0.2
                else:
                    # smaller ring
                    dr = 0.1
                    assert source_type == 'sring_J'
                r0 = 1
                x0 = 1.5
                y0 = 1.5
                J_factor = 10

            # elif source_option==3:
            #     # small circle in corner, seems less interesting
            #     r0 = 0.0
            #     dr = 0.05
            #     x0 = 0.9
            #     y0 = 0.9
            #     J_factor = 10
            else:
                raise NotImplementedError

        # note: some other currents give sympde or numba errors, see below [1]
        phi = J_factor * exp( - .5*(( (x-x0)**2 + (y-y0)**2 - r0**2 )/dr)**2 )

        # previous J:
        # J_x = -J_factor * (y-y0) * exp( - .5*(( (x-x0)**2 + (y-y0)**2 - r0**2 )/dr)**2 )   # /(x**2 + y**2)
        # J_y =  J_factor * (x-x0) * exp( - .5*(( (x-x0)**2 + (y-y0)**2 - r0**2 )/dr)**2 )
        J_x = - (y-y0) * phi
        J_y =   (x-x0) * phi

        if dc_pbm:
            # J just above is div-free, for the divergence-constrained problem we add a gradient term (with hom. bc) in the source
            # phi = J_factor * (( (x-x0)**2 + (y-y0)**2 - r0**2 )/dr**2) * exp( - .5*(( (x-x0)**2 + (y-y0)**2 - r0**2 )/dr)**2 )
            dx_phi = - 2*(x-x0) * (( (x-x0)**2 + (y-y0)**2 - r0**2 )/dr**2) * phi
            dy_phi = - 2*(y-y0) * (( (x-x0)**2 + (y-y0)**2 - r0**2 )/dr**2) * phi
            J_x += dx_phi
            J_y += dy_phi
            grad_phi = Tuple(dx_phi, dy_phi)

        f = Tuple(J_x, J_y)

    else:
        raise ValueError(source_type)

    if E_ex is None:
        E_ref_filename = get_load_dir(method=method_ref, domain_name=domain_name,nc=nc_ref,deg=deg_ref,data='solutions')+E_ref_fn(source_type, N_diag, Psource=Psource_ref, dc_pbm=dc_bpm_ref)
        print("no exact solution for this test-case, looking for ref solution values in file "+E_ref_filename+ "...")
        if os.path.isfile(E_ref_filename):
            print("-- file found")
            with open(E_ref_filename, 'rb') as file:
                E_ref_vals = np.load(file)
                # check form of ref values
                # assert 'x_vals' in E_ref_vals; assert 'y_vals' in E_ref_vals
                E_ref_x_vals = E_ref_vals['x_vals']
                E_ref_y_vals = E_ref_vals['y_vals']
                assert isinstance(E_ref_x_vals, (list, np.ndarray)) and isinstance(E_ref_y_vals, (list, np.ndarray))
            E_ref_vals = [E_ref_x_vals, E_ref_y_vals]
        else:
            print("-- no file, skipping it")

    return f, E_bc, E_ref_vals, E_ex, p_ex, phi, grad_phi

# --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * ---
# --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * --- * ---

if __name__ == '__main__':

    import argparse

    parser = argparse.ArgumentParser(
        formatter_class = argparse.ArgumentDefaultsHelpFormatter,
        description     = "Solve 2D curl-curl eigenvalue or source problem."
    )

    parser.add_argument('ncells',
        type = int,
        help = 'Number of cells in every patch'
    )

    parser.add_argument('degree',
        type = int,
        help = 'Polynomial spline degree'
    )

    parser.add_argument( '--domain',
        choices = ['square_2', 'square_6', 'square_8', 'square_9', 'annulus', 'curved_L_shape', 'pretzel', 'pretzel_f', 'pretzel_annulus', 'pretzel_debug'],
        default = 'curved_L_shape',
        help    = 'Domain'
    )

    parser.add_argument( '--method',
        choices = ['conga', 'nitsche'],
        default = 'conga',
        help    = 'Maxwell solver'
    )

    parser.add_argument( '--k',
        type    = int,
        choices = [-1, 0, 1],
        default = 1,
        help    = 'type of Nitsche IP method (NIP, IIP, SIP)'
    )

    parser.add_argument( '--DG_full',
        action  = 'store_true',
        help    = 'whether DG (Nitsche) method is used with full polynomials spaces'
    )

    parser.add_argument( '--proj_sol',
        action  = 'store_true',
        help    = 'whether cP1 is applied to solution of source problem'
    )

    parser.add_argument( '--no_plots',
        action  = 'store_true',
        help    = 'whether plots are done'
    )

    parser.add_argument( '--skip_err_u',
        action  = 'store_true',
        help    = 'skip the errors on u/E'
    )

    parser.add_argument( '--skip_vf',
        action  = 'store_true',
        help    = 'skip vector field plots'
    )

    parser.add_argument( '--hide_plots',
        action  = 'store_true',
        help    = 'whether plots are hidden'
    )

    parser.add_argument( '--show_curl_u',
        action  = 'store_true',
        help    = 'whether to plot the curl of u'
    )

    parser.add_argument( '--gamma',
        type    = float,
        default = 10,
        help    = 'penalization factor (Nitsche or conga)'
    )

    parser.add_argument( '--penal_regime',
        type    = int,
        choices = [0, 1, 2],
        default = 1,
        help    = 'penalization regime (Nitsche or conga)'
    )

    parser.add_argument( '--geo_cproj',
        action  = 'store_true',
        help    = 'whether cP is applied with the geometric (interpolation/histopolation) splines'
    )

    parser.add_argument( '--problem',
        choices = ['eigen_pbm', 'source_pbm'],
        default = 'source_pbm',
        help    = 'problem to be solved'
    )

    parser.add_argument( '--source',
        choices = ['manu_J', 'dipcurl_J', 'ellip_J', 'ring_J', 'sring_J', 'df_J', 'cf_J'],
        default = 'manu_J',
        help    = 'type of source (manufactured or circular J)'
    )

    parser.add_argument( '--Psource',
        choices = ['P1s', 'L2s'],
        default= 'L2s',
        help    = 'source approximation operator in V1h: commuting projection P1 or L2 projection'
    )

    parser.add_argument( '--save_E_vals',
        action  = 'store_true',
        help    = 'save the values of E on cdiag grid, for further comparisons'
    )

    parser.add_argument( '--eta',
        type    = float,
        default = 0,
        help    = 'factor of zero-order term in operator. Corresponds to -omega^2 for Maxwell harmonic'
    )

    parser.add_argument( '--mu',
        type    = float,
        default = 1,
        help    = 'factor of curl curl term in operator'
    )

    parser.add_argument( '--nu',
        type    = float,
        default = 0,
        help    = 'factor of -grad div term in operator'
    )

    parser.add_argument( '--sigma',
        type    = float,
        default = 0,
        help    = 'ref value around which eigenvalues are sought'
    )

    parser.add_argument( '--nb_eigs',
        type    = int,
        default = 20,
        help    = 'number of eigenvalues to find'
    )

    parser.add_argument( '--c_grad',
        type    = float,
        default = 1,
        help    = 'factor of grad term in ellip_J source for dc_pbm'
    )

    parser.add_argument( '--dc_pbm',
        action  = 'store_true',
        help    = 'curl-curl pbm with div-free constraint and Lagrange multiplier p [[WIP -- shunts some of the rest]]'
    )

    parser.add_argument( '--P1_dc',
        action  = 'store_true',
        help    = 'variant for div-free constraint (for Conga, apply it on cP1_hom @ sol)'
    )


    # Read input arguments
    args         = parser.parse_args()
    deg          = args.degree
    nc           = args.ncells
    domain_name  = args.domain
    method       = args.method
    k            = args.k
    DG_full      = args.DG_full
    geo_cproj    = args.geo_cproj
    gamma        = args.gamma
    penal_regime = args.penal_regime
    proj_sol     = args.proj_sol
    problem      = args.problem
    nb_eigs      = args.nb_eigs
    sigma        = args.sigma
    source_type  = args.source
    Psource      = args.Psource
    eta          = args.eta
    mu           = args.mu
    nu           = args.nu
    dc_pbm       = args.dc_pbm
    c_grad       = args.c_grad
    P1_dc        = args.P1_dc
    no_plots     = args.no_plots
    hide_plots   = args.hide_plots
    skip_vf      = args.skip_vf
    show_curl_u  = args.show_curl_u
    skip_err_u   = args.skip_err_u
    save_E_vals  = args.save_E_vals

    do_plots = not no_plots

    ncells = [nc, nc]
    degree = [deg,deg]

    if dc_pbm and problem=='eigen_pbm':
        raise NotImplementedError

    if domain_name in ['pretzel', 'pretzel_f'] and nc > 8:
        # backend_language='numba'
        backend_language='python'
    else:
        backend_language='python'
    print('[note: using '+backend_language+ ' backends in discretize functions]')

    # if DG_full:
    #     raise NotImplementedError("DG_full spaces not implemented yet (eval error in sympde/topology/mapping.py)")
    t_overstamp = time_count()  # full run
    t_stamp = time_count()

    print()
    print('--------------------------------------------------------------------------------------------------------------')
    if dc_pbm:
        print(' solving div-constrained source problem')
        print('     grad p + A u = f')
        print('            div u = 0')
    else:
        print(' solving '+problem)
        if problem == 'source_pbm':
            print('     A u = f')
        elif problem == 'eigen_pbm':
            print('     A u = sigma * u')
        else:
            raise ValueError(problem)
    print(' with: ')
    print(' - operator:     A u = ({0}) * u + ({1}) * curl curl u - ({2}) * grad div u'.format(eta, mu, nu))
    print(' - domain:       '+domain_name)
    if problem == 'source_pbm':
        print(' - source:       '+source_type)
    else:
        print(' - nb of eigs:   '+repr(nb_eigs))
        print(' - around sigma: '+repr(sigma))
    print(' - method:       '+get_method_name(method, k, geo_cproj))
    print()
        #
    domain = build_multipatch_domain(domain_name=domain_name)
    #
    print(' - nb patches:             '+repr(len(domain.interior)))
    print(' - nb of cells per patch:  '+repr(ncells))
    print(' - spline degree:          '+repr(degree))
    fem_name = get_fem_name(method=method, DG_full=DG_full, geo_cproj=geo_cproj, k=k, domain_name=domain_name,nc=nc,deg=deg)
    print(' [full scheme:   '+fem_name+' ]')
    print('--------------------------------------------------------------------------------------------------------------')
    print()

    mappings = OrderedDict([(P.logical_domain, P.mapping) for P in domain.interior])
    mappings_list = list(mappings.values())
    time_count(t_stamp)

    # plotting and diagnostics
    if domain_name == 'curved_L_shape':
        N_diag = 200
    else:
        N_diag = 100  # should match the grid resolution of the stored E_ref...

    # jump penalization factor:
    assert gamma >= 0

    h = 1/nc
    if penal_regime == 0:
        # constant penalization
        gamma_h = gamma
    elif penal_regime == 1:
        gamma_h = gamma/h
    elif penal_regime == 2:
        gamma_h = gamma * (deg+1)**2 /h  # DG std (see eg Buffa, Perugia and Warburton)
    else:
        raise ValueError(penal_regime)

    # harmonic fields storage
    need_harmonic_fields = (problem == 'source_pbm') and dc_pbm and (eta == 0) and (domain_name in ['pretzel_f', 'pretzel'])
    keep_harmonic_fields = (problem == 'eigen_pbm') and (sigma == 0) and (domain_name in ['pretzel', 'pretzel_f'])
    if need_harmonic_fields or keep_harmonic_fields:
        load_dir = get_load_dir(method=method, domain_name=domain_name, nc=nc, deg=deg, data='matrices')
        hf_filename = load_dir+hf_fn()
        if keep_harmonic_fields:
            if not os.path.exists(load_dir):
                os.makedirs(load_dir)
        if need_harmonic_fields:
            if os.path.isfile(hf_filename):
                print("getting harmonic fields (coefs) from file "+hf_filename)
                with open(hf_filename, 'rb') as file:
                    content = np.load(hf_filename)
                harmonic_fields = []
                for i in range(3):
                    # load coefs of the 3 discrete harmonic fields for the pretzel
                    harmonic_fields.append( content['hf_'+repr(i)] )
                    # print(type(harmonic_fields[i]))
                    # print(harmonic_fields[i].shape)

    # node based grid (to better see the smoothness)
    etas, xx, yy = get_plotting_grid(mappings, N=N_diag)
    grid_vals_hcurl = lambda v: get_grid_vals_vector(v, etas, mappings_list, space_kind='hcurl')
    grid_vals_l2 = lambda v: get_grid_vals_scalar(v, etas, mappings_list, space_kind='l2')

    # cell-centered grid to compute approx L2 norm
    etas_cdiag, xx_cdiag, yy_cdiag, patch_logvols = get_plotting_grid(mappings, N=N_diag, centered_nodes=True, return_patch_logvols=True)
    grid_vals_h1_cdiag = lambda v: get_grid_vals_scalar(v, etas_cdiag, mappings_list, space_kind='h1')
    grid_vals_hcurl_cdiag = lambda v: get_grid_vals_vector(v, etas_cdiag, mappings_list, space_kind='hcurl')

    # todo: add some identifiers for secondary parameters (eg gamma_h, proj_sol ...)
    fem_name = get_fem_name(method=method, DG_full=DG_full, geo_cproj=geo_cproj, k=k, domain_name=domain_name,nc=nc,deg=deg)
    rhs_name = rhs_fn(source_type,eta=eta,mu=mu,nu=nu,dc_pbm=dc_pbm,c_grad=c_grad,Psource=Psource,npz_suffix=False,prefix=False)
    plot_dir = './plots/'+rhs_name+'_'+fem_name+'/'
    if not os.path.exists(plot_dir):
        os.makedirs(plot_dir)

    print('discretizing the domain with ncells = '+repr(ncells)+'...' )
    domain_h = discretize(domain, ncells=ncells, comm=comm)

    t_stamp = time_count()
    print('discretizing the de Rham seq with degree = '+repr(degree)+'...' )
    derham  = Derham(domain, ["H1", "Hcurl", "L2"])
    derham_h = discretize(derham, domain_h, degree=degree, backend=PSYDAC_BACKENDS[backend_language])
    V0h = derham_h.V0
    V1h = derham_h.V1
    V2h = derham_h.V2
    nquads = [4*(d + 1) for d in degree]
    P0, P1, P2 = derham_h.projectors(nquads=nquads)
    # print(V1h.nbasis)
    # exit()

    # getting CONGA matrices -- may also needed with nitsche method, depending on the options ?  (eg M1_m)
    load_dir = get_load_dir(method='conga', domain_name=domain_name, nc=nc, deg=deg, data='matrices')
    M_mats, P_mats, D_mats, IK_mats = get_elementary_conga_matrices(
        domain_h, derham_h, load_dir=load_dir, backend_language=backend_language,
        discard_non_hom_matrices=(source_type in ['ellip_J', 'ring_J', 'sring_J'])
    )
    [M0_m, M1_m, M2_m, M0_minv] = M_mats
    [bsp_P0_m, bsp_P1_m, bsp_P0_hom_m, bsp_P1_hom_m] = P_mats  # BSpline-based conf Projections
    [bD0_m, bD1_m] = D_mats
    [I1_m, K0, K0_inv, K1, K1_inv] = IK_mats

    gsp_P0_hom_m = K0_inv @ bsp_P0_hom_m @ K0
    gsp_P1_hom_m = K1_inv @ bsp_P1_hom_m @ K1
    gsp_P0_m = K0_inv @ bsp_P0_m @ K0
    gsp_P1_m = K1_inv @ bsp_P1_m @ K1

    if geo_cproj:
        print(' [* GSP-conga: using Geometric Spline conf Projections ]')
        cP0_hom_m = gsp_P0_hom_m
        cP0_m     = gsp_P0_m
        cP1_hom_m = gsp_P1_hom_m
        cP1_m     = gsp_P1_m
    else:
        print(' [* BSP-conga: using B-Spline conf Projections ]')
        cP0_hom_m = bsp_P0_hom_m
        cP0_m     = bsp_P0_m
        cP1_hom_m = bsp_P1_hom_m
        cP1_m     = bsp_P1_m

    # weak divergence matrices V1h -> V0h
    pw_div_m = - M0_minv @ bD0_m.transpose() @ M1_m   # patch-wise weak divergence
    bsp_D0_m = bD0_m @ bsp_P0_hom_m  # bsp-conga gradient on homogeneous space
    bsp_div_m = - M0_minv @ bsp_D0_m.transpose() @ M1_m   # gsp-conga divergence
    gsp_D0_m = bD0_m @ gsp_P0_hom_m  # gsp-conga gradient on homogeneous space
    gsp_div_m = - M0_minv @ gsp_D0_m.transpose() @ M1_m   # bsp-conga divergence

    def div_norm(u_c, type=None):
        if type is None:
            if geo_cproj:
                type = 'gsp'
            else:
                type = 'bsp'
        if type=='gsp':
            du_c = gsp_div_m.dot(u_c)
        elif type=='bsp':
            du_c = bsp_div_m.dot(u_c)
        elif type=='pw':
            du_c = pw_div_m.dot(u_c)
        else:
            print("WARNING: invalid value for weak divergence type (returning -1)")
            return -1

        return np.dot(du_c,M0_m.dot(du_c))**0.5

    def curl_norm(u_c):
        du_c = (bD1_m @ cP1_m).dot(u_c)
        return np.dot(du_c,M2_m.dot(du_c))**0.5

    # E_vals saved/loaded as point values on cdiag grid (mostly for error measure)
    f = None
    E_ex = None
    E_ref_vals = None
    E_vals_filename = None

    # Eh saved/loaded as numpy array of FEM coefficients ?
    # save_Eh = False
    Eh = None

    if problem == 'source_pbm':

        print("***  Defining the source and ref solution *** ")

        # source and ref solution
        # nc_ref = 32
        # deg_ref = 6
        # nc_ref = 16
        nc_ref = 8
        deg_ref = 4
        Psource_ref = 'P1s'
        dc_bpm_ref = True
        # Psource_ref = 'L2s'
        method_ref = 'conga'

        f, E_bc, E_ref_vals, E_ex, p_ex, phi, grad_phi = get_source_and_solution(
            source_type=source_type, eta=eta, mu=mu, nu=nu, domain=domain,
            refsol_params=[nc_ref, deg_ref, N_diag, method_ref, Psource_ref, dc_bpm_ref], dc_pbm=dc_pbm, c_grad=c_grad
        )

        if E_ref_vals is None:
            print('-- no ref solution found')

        if save_E_vals:
            solutions_dir = get_load_dir(method=method, DG_full=DG_full, domain_name=domain_name,nc=nc,deg=deg,data='solutions')
            E_vals_filename = solutions_dir+E_ref_fn(source_type, N_diag, Psource=Psource, dc_pbm=dc_pbm)
            print( "for further comparisons, will save E_vals in "+E_vals_filename)
            if not os.path.exists(solutions_dir):
                os.makedirs(solutions_dir)

        # disabled for now -- if with want to save the coeffs we need to store more parameters (gamma, proj_sol, etc...)
        # save_Eh = False
        # if save_Eh:
        #     Eh_filename = solutions_dir+Eh_coeffs_fn(source_type, N_diag)
        #     print("-- I will also save the present solution coefficients in file '"+Eh_filename+"' --")
        #     if not os.path.exists(solutions_dir):
        #         os.makedirs(solutions_dir)

        hom_bc = (E_bc is None)
    else:
        # eigenpbm is with homogeneous bc
        E_bc = None
        hom_bc = True

    # ------------------------------------------------------------------------------------------
    #   operator matrices
    # ------------------------------------------------------------------------------------------


    if method == 'conga':
        CC_m, CC_bc_m, GD_m, GD_bc_m, JP_m = conga_operators_2d(M1_m=M1_m, M2_m=M2_m, cP1_m=cP1_m, cP1_hom_m=cP1_hom_m, bD1_m=bD1_m, I1_m=I1_m,
                                                 hom_bc=hom_bc, need_GD_matrix=(nu != 0))
        M_m = M1_m

    elif method == 'nitsche':
        # define the DG spaces
        if DG_full:
            V_dg  = VectorFunctionSpace('V_dg', domain, kind='Hcurl')
            W_dg  = VectorFunctionSpace('W_dg', domain, kind='H1')  # discretization hack
            Vh_dg = discretize(W_dg, domain_h, degree=degree, basis='B') # should be full-degree
            Vh_dg.symbolic_space = V_dg  # so that it's defined by 1-form push-forwards        ####  -> but doesnt work (13 oct)
            print('Vh_dg.degree = ', Vh_dg.degree)
            print('V1h.degree = ', V1h.degree)
        else:
            Vh_dg = V1h

        if dc_pbm:
            Qh_dg = V0h
        else:
            Qh_dg = None

        load_dir = get_load_dir(method='nitsche', DG_full=DG_full, domain_name=domain_name, nc=nc, deg=deg)
        CC_m, GD_m, D_m, JP_m, JPQ_m, M_m = nitsche_operators_2d(domain_h, Vh=Vh_dg, Qh=Qh_dg, k=k, load_dir=load_dir,
                                               need_D_matrix=dc_pbm, need_JPQ_matrix=dc_pbm,
                                               need_GD_matrix=(nu != 0),
                                               need_mass_matrix=DG_full, backend_language=backend_language)

        if not DG_full:
            M_m = M1_m
        CC_bc_m = None # we don't lift the BC with Nitsche
    else:
        raise ValueError(method)

    A_m = mu*CC_m + gamma_h*JP_m

    if eta != 0:
        # zero-order term
        if method == 'conga':
            # note: this filtering of M1_m is important (in particular in the presence of BCs)
            A_m += eta * cP1_hom_m.transpose() @ M1_m @ cP1_hom_m
        else:
            A_m += eta * M_m

    if nu != 0:
        A_m -= nu*GD_m

    if dc_pbm:
        # building operator for divergence-constrained pbm

        if method == 'conga':
            I0_m = IdLinearOperator(V0h).to_sparse_matrix()
            jump_penal_V0_hom_m = I0_m-cP0_hom_m
            JP0_m = jump_penal_V0_hom_m.transpose() * M0_m * jump_penal_V0_hom_m

            G_m = M1_m @ bD0_m @ cP0_hom_m # stiffness matrix of gradient operator, with penalization
            if P1_dc:
                print(" [P1_dc]: filtering the gradient operator ")
                G_m = cP1_hom_m.transpose() @ G_m
        else:
            # try with Nitsche ?
            JP0_m = JPQ_m
            G_m = -D_m.transpose()

        if need_harmonic_fields:
            tilde_hf = []
            for i in range(3):
                # print(type(harmonic_fields[i]))
                # print(harmonic_fields[i].shape)
                hi_c = harmonic_fields[i]  # coefs the of the i-th harmonic field, in the B/M spline basis of V1h
                tilde_hf.append( M_m @ hi_c )
            HC_m = bmat(tilde_hf)
            DCA_m = bmat([[A_m, G_m, HC_m.transpose()], [-G_m.transpose(), gamma_h * JP0_m, None], [-HC_m, None, None]])
        else:
            DCA_m = bmat([[A_m, G_m], [-G_m.transpose(), gamma_h * JP0_m]])
    else:
        DCA_m = None

    # norm of various operators (scheme dependent)
    def curl_curl_norm(u_c):
        du_c = CC_m.dot(u_c)
        #print("norm((bD1_m @ cP1_hom_m).dot(u_c)) = ", norm((bD1_m @ cP1_hom_m).dot(u_c)))
        du_c = CC_m.dot(u_c)
        return np.dot(du_c,M1_m.dot(du_c))**0.5

    def grad_div_norm(u_c):
        if nu != 0:
            du_c = GD_m.dot(u_c)
            return np.dot(du_c,M1_m.dot(du_c))**0.5
        else:
            print("-- cannot compute grad_div_norm")
            return -1


    # lifting of BC
    lift_E_bc = (problem == 'source_pbm' and method == 'conga' and not hom_bc and not dc_pbm)
    if lift_E_bc:
        # operator for bc lifting
        assert CC_bc_m is not None
        A_bc_m = eta * cP1_hom_m.transpose() @ M1_m @ cP1_m + mu*CC_bc_m
    else:
        A_bc_m = None

    # if not DG_full:
    #     div_CC = bsp_D0_m.transpose() @ CC_m
    #     print('****   [[[ spnorm(div_CC) ]]] :', spnorm(div_CC))

    if problem == 'eigen_pbm':

        print("***  Solving eigenvalue problem  *** ")
        if hom_bc:
            print('     (with homogeneous bc)')

        # todo: update this function
        # sigma, ref_sigmas = get_ref_eigenvalues(domain_name, operator)
        # nb_eigs = max(10, len(ref_sigmas))
        ref_sigmas = []
        # sigma = 50
        # nb_eigs = 20


        eigenvalues, eigenvectors = get_eigenvalues(nb_eigs, sigma, A_m, M_m)

        if keep_harmonic_fields:
            print("saving the 3 harmonic fields (coefs) in file "+hf_filename)
            with open(hf_filename, 'wb') as file:
                np.savez(file, hf_0=eigenvectors[:,0], hf_1=eigenvectors[:,1], hf_2=eigenvectors[:,2])

        eig_filter = False
        if eig_filter:
            # discard zero eigenvalues ? # todo: clean this filter
            n = 0
            all_eigenvalues = eigenvalues
            eigenvalues = []
            while len(eigenvalues) < len(ref_sigmas):
                comment = '* checking computed eigenvalue #{:d}: {:15.10f}: '.format(n, all_eigenvalues[n])
                if n == len(all_eigenvalues):
                    print("Error: not enough computed eigenvalues...")
                    raise ValueError
                if abs(all_eigenvalues[n]) > 1e-6:
                    eigenvalues.append(all_eigenvalues[n])
                    print(comment+'keeping it')
                else:
                    print(comment+'discarding small eigenvalue')
                n += 1

        errors = []

        n_errs = min(len(ref_sigmas), len(eigenvalues))
        for n in range(n_errs):
            errors.append(abs(eigenvalues[n]-ref_sigmas[n]))

        print('errors from reference eigenvalues: ')
        print(errors)

    elif problem == 'source_pbm':

        print("***  Solving source problem  *** ")

        # ------------------------------------------------------------------------------------------
        #   assembling RHS
        # ------------------------------------------------------------------------------------------

        rhs_load_dir = get_load_dir(domain_name=domain_name,nc=nc,deg=deg,DG_full=DG_full,data='rhs')
        if not os.path.exists(rhs_load_dir):
            os.makedirs(rhs_load_dir)

        rhs_filename = rhs_load_dir+rhs_fn(source_type,eta=eta,mu=mu,nu=nu,dc_pbm=dc_pbm, c_grad=c_grad, Psource=Psource)
        if os.path.isfile(rhs_filename):
            print("getting rhs array from file "+rhs_filename)
            with open(rhs_filename, 'rb') as file:
                content = np.load(rhs_filename)
            b_c = content['b_c']
        else:
            print("-- no rhs file '"+rhs_filename+" -- so I will assemble the source")

            if Psource == 'P1s':
                # J_h = P1-geometric (commuting) projection of J
                # P0, P1, P2 = derham_h.projectors(nquads=nquads)
                f_x = lambdify(domain.coordinates, f[0])
                f_y = lambdify(domain.coordinates, f[1])
                f_log = [pull_2d_hcurl([f_x, f_y], m) for m in mappings_list]
                f_h = P1(f_log)
                f_c = f_h.coeffs.toarray()
                b_c = M1_m.dot(f_c)

            else:
                # J_h = L2 projection of J
                v  = element_of(V1h.symbolic_space, name='v')
                expr = dot(f,v)
                l = LinearForm(v, integral(domain, expr))
                lh = discretize(l, domain_h, V1h, backend=PSYDAC_BACKENDS[backend_language])
                b  = lh.assemble()
                b_c = b.toarray()

            print("saving this rhs arrays (for future needs) in file "+rhs_filename)
            with open(rhs_filename, 'wb') as file:
                np.savez(file, b_c=b_c)

        # if method == 'conga':
        #     print("FILTERING RHS (FOR CONGA)")
        #     b_c = cP1_hom_m.transpose().dot(b_c)

        if method == 'nitsche' and not hom_bc:
            print("(non hom.) bc with nitsche: need some additional rhs arrays.")
            # need additional terms for the bc with nitsche
            rhs_filename = rhs_load_dir+rhs_fn(source_type, nbc=True, eta=eta,mu=mu,nu=nu,dc_pbm=dc_pbm, c_grad=c_grad, Psource=Psource)
            if os.path.isfile(rhs_filename):
                print("getting them from file "+rhs_filename)
                with open(rhs_filename, 'rb') as file:
                    content = np.load(rhs_filename)
                bs_c = content['bs_c']
                bp_c = content['bp_c']  # penalization term
            else:
                print("-- no rhs file '"+rhs_filename+" -- so I will assemble them...")
                nn  = NormalVector('nn')
                boundary = domain.boundary
                v  = element_of(V1h.symbolic_space, name='v')

                # expr_b = -k*cross(nn, E_bc)*curl(v) + gamma_h * cross(nn, E_bc) * cross(nn, v)

                # nitsche symmetrization term:
                expr_bs = cross(nn, E_bc)*curl(v)
                ls = LinearForm(v, integral(boundary, expr_bs))
                lsh = discretize(ls, domain_h, V1h, backend=PSYDAC_BACKENDS[backend_language])
                bs  = lsh.assemble()
                bs_c = bs.toarray()

                # nitsche penalization term:
                expr_bp = cross(nn, E_bc) * cross(nn, v)
                lp = LinearForm(v, integral(boundary, expr_bp))
                lph = discretize(lp, domain_h, V1h, backend=PSYDAC_BACKENDS[backend_language])
                bp  = lph.assemble()
                bp_c = bp.toarray()

                print("saving these rhs arrays (for future needs) in file "+rhs_filename)
                with open(rhs_filename, 'wb') as file:
                    np.savez(file, bs_c=bs_c, bp_c=bp_c)

            # full rhs for nitsche method with non-hom. bc
            b_c = b_c + mu*(-k*bs_c) + gamma_h*bp_c


        if lift_E_bc:
            t_stamp = time_count(t_stamp)
            print('lifting the boundary condition...')
            debug_plot = False

            # Projector on broken space
            # todo: we should probably apply P1 on E_bc -- it's a bit weird to call it on the list of (pulled back) logical fields.
            # P0, P1, P2 = derham_h.projectors(nquads=nquads)
            E_bc_x = lambdify(domain.coordinates, E_bc[0])
            E_bc_y = lambdify(domain.coordinates, E_bc[1])
            E_bc_log = [pull_2d_hcurl([E_bc_x, E_bc_y], f) for f in mappings_list]
            # note: we only need the boundary dofs of E_bc (and Eh_bc)
            Eh_bc = P1(E_bc_log)
            Ebc_c = Eh_bc.coeffs.toarray()

            if debug_plot:
                Ebc_x_vals, Ebc_y_vals = grid_vals_hcurl(Eh_bc)
                my_small_plot(
                    title=r'full E for bc',
                    vals=[Ebc_x_vals, Ebc_y_vals],
                    titles=[r'Eb x', r'Eb y'],  # , r'$div_h J$' ],
                    surface_plot=False,
                    xx=xx, yy=yy,
                    save_fig=plot_dir+'full_Ebc.png',
                    hide_plot=hide_plots,
                    cmap='plasma',
                    dpi=400,
                )

            # removing internal dofs
            Ebc_c = cP1_m.dot(Ebc_c)-cP1_hom_m.dot(Ebc_c)
            b_c = b_c - A_bc_m.dot(Ebc_c)

            if debug_plot:
                Eh_bc = FemField(V1h, coeffs=array_to_stencil(Ebc_c, V1h.vector_space))
                Ebc_x_vals, Ebc_y_vals = grid_vals_hcurl(Eh_bc)
                my_small_plot(
                    title=r'E bc',
                    vals=[Ebc_x_vals, Ebc_y_vals],
                    titles=[r'Eb x', r'Eb y'],  # , r'$div_h J$' ],
                    surface_plot=False,
                    xx=xx, yy=yy,
                    save_fig=plot_dir+'Ebc.png',
                    hide_plot=hide_plots,
                    cmap='plasma',
                    dpi=400,
                )

                E_ex_x = lambdify(domain.coordinates, E_ex[0])
                E_ex_y = lambdify(domain.coordinates, E_ex[1])
                E_ex_log = [pull_2d_hcurl([E_ex_x, E_ex_y], f) for f in mappings_list]
                # note: we only need the boundary dofs of E_bc (and Eh_bc)
                Eh_ex = P1(E_ex_log)
                E_ex_c = Eh_ex.coeffs.toarray()

                E_diff_c = E_ex_c - Ebc_c
                Edh = FemField(V1h, coeffs=array_to_stencil(E_diff_c, V1h.vector_space))
                Ed_x_vals, Ed_y_vals = grid_vals_hcurl(Edh)
                my_small_plot(
                    title=r'E_exact - E_bc',
                    vals=[Ed_x_vals, Ed_y_vals],
                    titles=[r'(E_{ex}-E_{bc})_x', r'(E_{ex}-E_{bc})_y'],  # , r'$div_h J$' ],
                    surface_plot=False,
                    xx=xx, yy=yy,
                    save_fig=plot_dir+'diff_Ebc.png',
                    hide_plot=hide_plots,
                    cmap='plasma',
                    dpi=400,
                )

        print(' [[ source divergence: ')
        fh_c = spsolve(M1_m.tocsc(), b_c)
        fh_norm = np.dot(fh_c,M1_m.dot(fh_c))**0.5
        print("|| fh || = ", fh_norm)
        print("|| pw_div fh || / || fh ||  = ", div_norm(fh_c, type='pw')/fh_norm)
        print("|| bsp_div fh || / || fh || = ", div_norm(fh_c, type='bsp')/fh_norm)
        print("|| gsp_div fh || / || fh || = ", div_norm(fh_c, type='gsp')/fh_norm)
        print(' ]] ')

        print(' [[ source curl: ')
        print("|| curl fh || / || fh ||  = ", curl_norm(fh_c)/fh_norm)
        print(' ]] ')

        print(' [[ more info on source: ')
        print("norm((bD1_m @ cP1_m).dot(fh_c))     = ", norm((bD1_m @ cP1_m).dot(fh_c)))
        print("norm((bD1_m @ cP1_hom_m).dot(fh_c)) = ", norm((bD1_m @ cP1_hom_m).dot(fh_c)))
        print("|| curl curl fh || = ", curl_curl_norm(fh_c))
        print("|| grad div fh ||  = ", grad_div_norm(fh_c))
        print(' ]] ')

        plot_source = True
        # plot_source = False
        if do_plots and plot_source:
            t_stamp = time_count(t_stamp)
            print('plotting the source...')
            # representation of discrete source:
            fh = FemField(V1h, coeffs=array_to_stencil(fh_c, V1h.vector_space))

            fh_x_vals, fh_y_vals = grid_vals_hcurl(fh)
            plot_full_fh=False
            if plot_full_fh:
                div_fh = FemField(V0h, coeffs=array_to_stencil(div_m.dot(fh_c), V0h.vector_space))
                div_fh_vals = grid_vals_h1(div_fh)
                my_small_plot(
                    title=r'discrete source term for Maxwell curl-curl problem',
                    vals=[np.abs(fh_x_vals), np.abs(fh_y_vals), np.abs(div_fh_vals)],
                    titles=[r'$|fh_x|$', r'$|fh_y|$', r'$|div_h fh|$'],  # , r'$div_h J$' ],
                    cmap='hsv',
                    save_fig=plot_dir+'full_Jh.png',
                    hide_plot=hide_plots,
                    surface_plot=False,
                    xx=xx, yy=yy,
                )
            else:
                abs_fh_vals = [np.sqrt(abs(fx)**2 + abs(fy)**2) for fx, fy in zip(fh_x_vals, fh_y_vals)]
                my_small_plot(
                    title=r'current source $J_h$ (amplitude)',
                    vals=[abs_fh_vals],
                    titles=[r'$|J_h|$'],  # , r'$div_h J$' ],
                    surface_plot=False,
                    xx=xx, yy=yy,
                    save_fig=plot_dir+'Jh.png',
                    hide_plot=hide_plots,
                    cmap='plasma',
                    dpi=400,
                )
            if not skip_vf:
                my_small_streamplot(
                    title=r'current source $J_h$ (vector field)',
                    vals_x=fh_x_vals,
                    vals_y=fh_y_vals,
                    skip=10,
                    xx=xx, yy=yy,
                    save_fig=plot_dir+'Jh_vf.png',
                    hide_plot=hide_plots,
                    amp_factor=2,
                )

            if phi is not None:

                # plot also phi:
                phi = lambdify(domain.coordinates, phi)
                phi_log = [pull_2d_h1(phi, m) for m in mappings_list]
                phi_h = P0(phi_log)

                phi_c = phi_h.coeffs.toarray()
                bcp_c = phi_c - cP0_hom_m @ phi_c
                bcp_h = FemField(V0h, coeffs=array_to_stencil(bcp_c, V0h.vector_space))

                bcp_vals = grid_vals_h1_cdiag(bcp_h)
                my_small_plot(
                    title=r'$|\phi_h - P^c \phi_h|$',
                    vals=[np.abs(bcp_vals)],
                    titles=[r'$|\phi_h - P^c \phi_h|$'],
                    xx=xx_cdiag,
                    yy=yy_cdiag,
                    save_fig=plot_dir+'abs_bcp.png',
                    hide_plot=hide_plots,
                    surface_plot=False
                    # gridlines_x1=gridlines_x1,
                    # gridlines_x2=gridlines_x2,
                )

                phi_vals = grid_vals_h1_cdiag(phi_h)
                my_small_plot(
                    title=r'discrete $\phi_h$',
                    vals=[phi_vals],
                    titles=[r'$\phi$'],
                    xx=xx_cdiag,
                    yy=yy_cdiag,
                    save_fig=plot_dir+'phi.png',
                    hide_plot=hide_plots,
                    surface_plot=False
                    # gridlines_x1=gridlines_x1,
                    # gridlines_x2=gridlines_x2,
                )


        # ------------------------------------------------------------------------------------------
        #   solving the matrix equation
        # ------------------------------------------------------------------------------------------

        if dc_pbm:
            AA_m = DCA_m.tocsc()

            print("building block RHS for divergence-constrained problem")
            bp_c = np.zeros(V0h.nbasis)
            if need_harmonic_fields:
                bh_c = np.zeros(3)  # coefs of the harmonic part of the solution
                bb_c = np.block([b_c, bp_c, bh_c])
            else:
                bb_c = np.block([b_c, bp_c])
        else:
            AA_m = A_m.tocsc()
            bb_c = b_c

        ref_Ab_save = False
        if ref_Ab_save:
            t_stamp = time_count(t_stamp)
            print('REF RUN: SAVING b_c in ref_b_c and AA_m in ref_AA_m...')
            bc_filename = 'ref_bb_c'
            with open(bc_filename, 'wb') as file:
                np.savez(file, ref_bb_c=bb_c)
            AA_filename = 'ref_AA_m'
            with open(bc_filename, 'wb') as file:
                np.savez(file, ref_AA_m=AA_m)

        t_stamp = time_count(t_stamp)
        try_solve = True
        if try_solve:
        # try:
            print("trying direct solve with scipy spsolve...")   #todo: use for small problems [[ or: try catch ??]]
            sol_c = spsolve(AA_m, bb_c)
        #
        # except Exception as e:
        else:
            # print("did not work (Exception: {})-- trying with scipy lgmres...".format(e))
            print(" -- solving with approximate inverse using ILU decomposition -- ")
            # A_csc = A_m.tocsc()
            AA_spilu = spilu(AA_m)
            # AA_spilu = spilu(AA_m, fill_factor=15, drop_tol=5e-5)  # better preconditionning, if matrix not too large
            # print('**** AA: ',  AA_m.shape )

            preconditioner = LinearOperator( AA_m.shape, lambda x: AA_spilu.solve(x) )
            nb_iter = 0
            def f2_iter(x):
                global nb_iter
                print('lgmres -- iter = ', nb_iter, 'residual= ', norm(AA_m.dot(x)-bb_c))
                nb_iter = nb_iter + 1
            tol = 1e-10
            sol_c, info = lgmres(AA_m, bb_c, x0=None, tol=tol, atol=tol, M=preconditioner, callback=f2_iter)
                      # inner_m=30, outer_k=3, outer_v=None,
                      #                                           store_outer_Av=True)
            print(' -- convergence info:', info)

        hh_c = np.zeros(V1h.nbasis)
        if dc_pbm:
            Eh_c = sol_c[:V1h.nbasis]
            ph_c = sol_c[V1h.nbasis:V1h.nbasis+V0h.nbasis]
            if need_harmonic_fields:
                # compute the harmonic part (h) of the solution
                hh_hc = sol_c[V1h.nbasis+V0h.nbasis:]  # coefs of the harmonic part, in the basis of the harmonic fields
                assert len(hh_hc) == 3
                for i in range(3):
                    hi_c = harmonic_fields[i]  # coefs the of the i-th harmonic field, in the B/M spline basis of V1h
                    hh_c += hh_hc[i]*hi_c
        else:
            Eh_c = sol_c
            ph_c = np.zeros(V0h.nbasis)

        # E_coeffs = array_to_stencil(Eh_c, V1h.vector_space)

        # print('**** cP1:',  cP1_hom_m.shape )
        # print('**** Eh:',  Eh_c.shape )
        print("... solver done.")
        time_count(t_stamp)

        if proj_sol:
            if method == 'conga':
                print("  (projecting the homogeneous Conga solution with cP1_hom_m)  ")
                Eh_c = cP1_hom_m.dot(Eh_c)
            else:
                print("  (projecting the Nitsche solution with cP1_m -- NOTE: THIS IS NONSTANDARD! )  ")
                Eh_c = cP1_m.dot(Eh_c)

        if lift_E_bc:
            print("lifting the solution with E_bc  ")
            Eh_c += Ebc_c


        Eh = FemField(V1h, coeffs=array_to_stencil(Eh_c, V1h.vector_space))

        if dc_pbm:
            ph = FemField(V0h, coeffs=array_to_stencil(ph_c, V0h.vector_space))
            hh = FemField(V1h, coeffs=array_to_stencil(hh_c, V1h.vector_space))

        # if save_Eh:
        #     # MCP: I think this should be discarded....
        #     if os.path.isfile(Eh_filename):
        #         print('(solution coeff array is already saved, no need to save it again)')
        #     else:
        #         print("saving solution coeffs (for future needs) in new file "+Eh_filename)
        #         with open(Eh_filename, 'wb') as file:
        #             np.savez(file, array_coeffs=Eh_c)

        #+++++++++++++++++++++++++++++++
        # plotting and diagnostics
        #+++++++++++++++++++++++++++++++

        compute_div = True
        if compute_div:
            print(' [[ field divergence: ')
            Eh_norm = np.dot(Eh_c,M1_m.dot(Eh_c))**0.5
            print("|| Eh || = ", Eh_norm)
            print("|| pw_div Eh || / || Eh ||  = ", div_norm(Eh_c, type='pw')/Eh_norm)
            print("|| bsp_div Eh || / || Eh || = ", div_norm(Eh_c, type='bsp')/Eh_norm)
            print("|| gsp_div Eh || / || Eh || = ", div_norm(Eh_c, type='bsp')/Eh_norm)
            print(' ]] ')

            print(' [[ field curl: ')
            print("|| curl Eh || / || Eh ||  = ", curl_norm(Eh_c)/Eh_norm)
            print(' ]] ')

            print(' [[ more info on solution: ')
            print("|| curl curl Eh || = ", curl_curl_norm(Eh_c))
            print("|| grad div Eh ||  = ", grad_div_norm(Eh_c))
            print(' ]] ')


        if do_plots:
            # smooth plotting with node-valued grid
            Eh_x_vals, Eh_y_vals = grid_vals_hcurl(Eh)
            Eh_abs_vals = [np.sqrt(abs(ex)**2 + abs(ey)**2) for ex, ey in zip(Eh_x_vals, Eh_y_vals)]
            my_small_plot(
                title=r'solution $E_h$ (amplitude) for $\eta = $'+repr(eta),
                vals=[Eh_abs_vals], #[Eh_x_vals, Eh_y_vals, Eh_abs_vals],
                titles=[r'$|E^h|$'], #[r'$E^h_x$', r'$E^h_y$', r'$|E^h|$'],
                xx=xx,
                yy=yy,
                surface_plot=False,
                # gridlines_x1=gridlines_x1,
                # gridlines_x2=gridlines_x2,
                # save_fig=plot_dir+'Eh.png',
                save_fig=plot_dir+'Eh_eta='+repr(eta)+'.png',
                hide_plot=hide_plots,
                cmap='hsv',
                dpi = 400,
            )
            if not skip_vf:
                my_small_streamplot(
                    title=r'solution $E_h$ (vector field) for $\eta = $'+repr(eta),
                    vals_x=Eh_x_vals,
                    vals_y=Eh_y_vals,
                    skip=10,
                    xx=xx,
                    yy=yy,
                    amp_factor=2,
                    # save_fig=plot_dir+'Eh_vf.png',
                    save_fig=plot_dir+'Eh_vf_eta='+repr(eta)+'.png',
                    hide_plot=hide_plots,
                    dpi = 200,
                )
            if need_harmonic_fields:
                hh_x_vals, hh_y_vals = grid_vals_hcurl(hh)
                hh_abs_vals = [np.sqrt(abs(ex)**2 + abs(ey)**2) for ex, ey in zip(hh_x_vals, hh_y_vals)]
                my_small_plot(
                    title=r'harmonic part of solution $h_h$ (amplitude)',
                    vals=[hh_abs_vals], #[Eh_x_vals, Eh_y_vals, Eh_abs_vals],
                    titles=[r'$|h^h|$'], #[r'$E^h_x$', r'$E^h_y$', r'$|E^h|$'],
                    xx=xx,
                    yy=yy,
                    surface_plot=False,
                    # gridlines_x1=gridlines_x1,
                    # gridlines_x2=gridlines_x2,
                    # save_fig=plot_dir+'Eh.png',
                    save_fig=plot_dir+'hh.png',
                    hide_plot=hide_plots,
                    cmap='hsv',
                    dpi = 400,
                )
                if not skip_vf:
                    my_small_streamplot(
                        title=r'harmonic part of solution $h_h$ (vector field)',
                        vals_x=hh_x_vals,
                        vals_y=hh_y_vals,
                        skip=10,
                        xx=xx,
                        yy=yy,
                        amp_factor=2,
                        # save_fig=plot_dir+'Eh_vf.png',
                        save_fig=plot_dir+'hh_vf.png',
                        hide_plot=hide_plots,
                        dpi = 200,
                    )


        if do_plots and show_curl_u:
            curl_u_c = (bD1_m @ cP1_m).dot(Eh_c)
            curl_uh = FemField(V2h, coeffs=array_to_stencil(curl_u_c, V2h.vector_space))
            curl_u_vals = grid_vals_l2(curl_uh)
            my_small_plot(
                title=r'$\nabla \times u_h$',
                vals=[curl_u_vals],
                titles=[r'$\nabla \times u_h$'],
                xx=xx,
                yy=yy,
                save_fig=plot_dir+'curl_u.png',
                hide_plot=hide_plots,
                surface_plot=False
                # gridlines_x1=gridlines_x1,
                # gridlines_x2=gridlines_x2,
            )

        exit()

        # error measure with centered-valued grid
        quad_weights = get_grid_quad_weights(etas_cdiag, patch_logvols, mappings_list)
        xx = xx_cdiag
        yy = yy_cdiag

        check_grad_phi = False
        if check_grad_phi and do_plots and (phi is not None) and dc_pbm and source_type in ['ellip_J', 'ring_J', 'sring_J']:

            # visual diff between grad p and grad phi
            # grad phi
            phi = lambdify(domain.coordinates, phi)
            phi_log = [pull_2d_h1(phi, m) for m in mappings_list]
            phi_h = P0(phi_log)

            # # plot phi:
            # phi_vals = grid_vals_h1_cdiag(phi_h)
            # my_small_plot(
            #     title=r'discrete $\phi_h$',
            #     vals=[phi_vals],
            #     titles=[r'$\phi$'],
            #     xx=xx,
            #     yy=yy,
            #     save_fig=plot_dir+'phi.png',
            #     hide_plot=hide_plots,
            #     surface_plot=True
            #     # gridlines_x1=gridlines_x1,
            #     # gridlines_x2=gridlines_x2,
            # )

            phi_c = phi_h.coeffs.toarray()
            dphi_c = c_grad * bD0_m @ phi_c
            dphi_h = FemField(V1h, coeffs=array_to_stencil(dphi_c, V1h.vector_space))  # grad phi (in source)

            # grad phi (in source)
            f2_x = lambdify(domain.coordinates, grad_phi[0])
            f2_y = lambdify(domain.coordinates, grad_phi[1])
            f2_log = [pull_2d_hcurl([f2_x, f2_y], m) for m in mappings_list]
            f2_h = P1(f2_log)
            # f2_c = f2_h.coeffs.toarray()
            # # b2_c = M1_m.dot(f2_c)
            # f2_h = FemField(V1h, coeffs=array_to_stencil(f2_c, V1h.vector_space))  # grad phi (in source)

            dp_c =  bD0_m @ cP0_m @ ph_c
            dp_h = FemField(V1h, coeffs=array_to_stencil(dp_c, V1h.vector_space))  # grad p (in sol)
            dp_x_vals, dp_y_vals = grid_vals_hcurl_cdiag(dp_h)
            dphi_x_vals, dphi_y_vals = grid_vals_hcurl_cdiag(dphi_h)
            f2_x_vals, f2_y_vals = grid_vals_hcurl_cdiag(f2_h)

            # compare dp and dphi
            dp_x_err = [(u1 - u2) for u1, u2 in zip(dphi_x_vals, dp_x_vals)]
            dp_y_err = [(u1 - u2) for u1, u2 in zip(dphi_y_vals, dp_y_vals)]
            my_small_plot(
                title=r'approximation of term $\nabla \phi$, $x$ component',
                vals=[dphi_x_vals, dp_x_vals, dp_x_err],
                titles=[r'$d_x \phi$', r'$d_x p$', r'diff'],
                xx=xx,
                yy=yy,
                save_fig=plot_dir+'err_dpx.png',
                hide_plot=hide_plots,
                surface_plot=True
                # gridlines_x1=gridlines_x1,
                # gridlines_x2=gridlines_x2,
            )
            my_small_plot(
                title=r'approximation of term $\nabla \phi$, $y$ component',
                vals=[dphi_y_vals, dp_y_vals, dp_y_err],
                titles=[r'$d_y \phi$', r'$d_x p$', r'diff'],
                xx=xx,
                yy=yy,
                save_fig=plot_dir+'err_dpy.png',
                hide_plot=hide_plots,
                surface_plot=True
                # gridlines_x1=gridlines_x1,
                # gridlines_x2=gridlines_x2,
            )
            dp_errors_cdiag = [np.sqrt( (e1)**2 + (e2)**2 )
                       for e1, e2 in zip(dp_x_err, dp_y_err)]
            l2_error = (np.sum([J_F * err**2 for err, J_F in zip(dp_errors_cdiag, quad_weights)]))**0.5

            err_message = '(grad p - grad phi) error: {}\n'.format(l2_error)
            print('\n** '+err_message)

            # compare dphi and f2
            dp_x_err = [(u1 - u2) for u1, u2 in zip(f2_x_vals, dp_x_vals)]
            dp_y_err = [(u1 - u2) for u1, u2 in zip(f2_y_vals, dp_y_vals)]
            my_small_plot(
                title=r'approximation of term $f_2$, $x$ component',
                vals=[f2_x_vals, dp_x_vals, dp_x_err],
                titles=[r'$f_{2,x}$', r'$d_x p$', r'diff'],
                xx=xx,
                yy=yy,
                save_fig=plot_dir+'err_f2x.png',
                hide_plot=hide_plots,
                surface_plot=True
                # gridlines_x1=gridlines_x1,
                # gridlines_x2=gridlines_x2,
            )
            my_small_plot(
                title=r'approximation of term $f_2$, $y$ component',
                vals=[f2_y_vals, dp_y_vals, dp_y_err],
                titles=[r'$f_{2,y}$', r'$d_x p$', r'diff'],
                xx=xx,
                yy=yy,
                save_fig=plot_dir+'err_f2y.png',
                hide_plot=hide_plots,
                surface_plot=True
                # gridlines_x1=gridlines_x1,
                # gridlines_x2=gridlines_x2,
            )
            dp_errors_cdiag = [np.sqrt( (e1)**2 + (e2)**2 )
                       for e1, e2 in zip(dp_x_err, dp_y_err)]
            l2_error = (np.sum([J_F * err**2 for err, J_F in zip(dp_errors_cdiag, quad_weights)]))**0.5

            err_message = '(grad p - f_2) error: {}\n'.format(l2_error)
            print('\n** '+err_message)

        Eh_x_vals, Eh_y_vals = grid_vals_hcurl_cdiag(Eh)
        if save_E_vals:
            print("saving solution values (on cdiag grid) in new file (for future needs)"+E_vals_filename)
            with open(E_vals_filename, 'wb') as file:
                np.savez(file, x_vals=Eh_x_vals, y_vals=Eh_y_vals)

        if not skip_err_u:

            if E_ref_vals is None:
                E_x_vals = np.zeros_like(Eh_x_vals)
                E_y_vals = np.zeros_like(Eh_y_vals)
            else:
                E_x_vals, E_y_vals = E_ref_vals

            only_last_patch = False
            Eh_errors_cdiag = [np.sqrt( (u1-v1)**2 + (u2-v2)**2 )
                               for u1, v1, u2, v2 in zip(E_x_vals, Eh_x_vals, E_y_vals, Eh_y_vals)]
            E_amps_cdiag = [np.sqrt( (u1)**2 + (u2)**2 )
                               for u1, u2 in zip(E_x_vals, E_y_vals)]

            if only_last_patch:
                print('WARNING ** WARNING : measuring error on last patch only !!' )
                warning_msg = ' [on last patch]'
                l2_error = (np.sum([J_F * err**2 for err, J_F in zip(Eh_errors_cdiag[-1:], quad_weights[-1:])]))**0.5
            else:
                warning_msg = ''
                l2_norm_E = (np.sum([J_F * val**2 for val, J_F in zip(E_amps_cdiag, quad_weights)]))**0.5
                l2_error  = (np.sum([J_F * val**2 for val, J_F in zip(Eh_errors_cdiag, quad_weights)]))**0.5

            err_message = 'grid diag '+warning_msg+' for method={0} with nc={1}, deg={2}, gamma_h={3}, proj_sol={4}: abs_error={5}, rel_error={6}\n'.format(
                        get_method_name(method, k, geo_cproj, penal_regime), nc, deg, gamma_h, proj_sol, l2_error, l2_error/l2_norm_E
            )
            print('\n** '+err_message)

            check_err = True
            if E_ex is not None:
                # also assembling the L2 error with Psydac quadrature
                print(" -- * --  also computing L2 error with explicit (exact) solution, using Psydac quadratures...")
                F  = element_of(V1h.symbolic_space, name='F')
                error       = Matrix([F[0]-E_ex[0],F[1]-E_ex[1]])
                l2_norm     = Norm(error, domain, kind='l2')
                l2_norm_h   = discretize(l2_norm, domain_h, V1h, backend=PSYDAC_BACKENDS[backend_language])
                l2_error     = l2_norm_h.assemble(F=Eh)
                err_message_2 = 'l2_psydac error for method = {0} with nc = {1}, deg = {2}, gamma = {3}, gamma_h = {4} and proj_sol = {5} [*] : {6}\n'.format(
                        get_method_name(method, k, geo_cproj, penal_regime), nc, deg, gamma, gamma_h, proj_sol, l2_error
                )
                print('\n** '+err_message_2)
                if check_err:
                    # since Ex is available, compute also the auxiliary error || Eh - P1 E || with M1 mass matrix
                    # P0, P1, P2 = derham_h.projectors(nquads=nquads)
                    E_x = lambdify(domain.coordinates, E_ex[0])
                    E_y = lambdify(domain.coordinates, E_ex[1])
                    E_log = [pull_2d_hcurl([E_x, E_y], f) for f in mappings_list]
                    Ex_h = P1(E_log)
                    Ex_c = Ex_h.coeffs.toarray()
                    err_c = Ex_c-Eh_c
                    err_norm = np.dot(err_c,M1_m.dot(err_c))**0.5
                    print('--- ** --- check: L2 discrete-error (in V1h): {}'.format(err_norm))

            else:
                err_message_2 = ''

            error_filename = error_fn(source_type=source_type, method=method, k=k, domain_name=domain_name,deg=deg)
            if not os.path.exists(error_filename):
                open(error_filename, 'w')
            with open(error_filename, 'a') as a_writer:
                a_writer.write(err_message)
                if err_message_2:
                    a_writer.write(err_message_2)

            if do_plots:
                E_x_err = [(u1 - u2) for u1, u2 in zip(E_x_vals, Eh_x_vals)]
                E_y_err = [(u1 - u2) for u1, u2 in zip(E_y_vals, Eh_y_vals)]
                my_small_plot(
                    title=r'approximation of solution $u$, $x$ component',
                    vals=[E_x_vals, Eh_x_vals, E_x_err],
                    titles=[r'$u^{ex}_x(x,y)$', r'$u^h_x(x,y)$', r'$|(u^{ex}-u^h)_x(x,y)|$'],
                    xx=xx,
                    yy=yy,
                    save_fig=plot_dir+'err_Ex.png',
                    hide_plot=hide_plots,
                    # gridlines_x1=gridlines_x1,
                    # gridlines_x2=gridlines_x2,
                )

                my_small_plot(
                    title=r'approximation of solution $u$, $y$ component',
                    vals=[E_y_vals, Eh_y_vals, E_y_err],
                    titles=[r'$u^{ex}_y(x,y)$', r'$u^h_y(x,y)$', r'$|(u^{ex}-u^h)_y(x,y)|$'],
                    xx=xx,
                    yy=yy,
                    save_fig=plot_dir+'err_Ey.png',
                    hide_plot=hide_plots,
                    # gridlines_x1=gridlines_x1,
                    # gridlines_x2=gridlines_x2,
                )



    else:
        raise NotImplementedError

    print(" -- OK run done -- ")
    time_count(t_overstamp, msg='full run')
    print()
    exit()







