import numpy as np
import pytest
import logging

from psydac.core.bsplines          import make_knots
from psydac.fem.basic              import FemField
from psydac.fem.splines            import SplineSpace
from psydac.fem.tensor             import TensorFemSpace
from psydac.feec.derivatives       import VectorCurl_2D, Divergence_2D
from psydac.feec.global_projectors import Projector_H1, Projector_L2
from psydac.feec.global_projectors import projection_matrix_H1_homogeneous_bc, projection_matrix_Hdiv_homogeneous_bc 
from psydac.feec.tests.magnetostatic_pbm_annulus import solve_magnetostatic_pbm_annulus
from psydac.ddm.cart               import DomainDecomposition

import numpy as np
import sympy
from typing import Tuple

from sympde.topology  import Derham, Square, IdentityMapping, PolarMapping
from sympde.topology.domain import Domain, Union, Connectivity

from psydac.feec.global_projectors import projection_matrix_Hdiv_homogeneous_bc, projection_matrix_H1_homogeneous_bc

from psydac.api.discretization import discretize
from psydac.api.feec import DiscreteDerham
from psydac.api.fem  import DiscreteBilinearForm, DiscreteLinearForm
from psydac.api.postprocessing import OutputManager, PostProcessManager
from psydac.cad.geometry     import Geometry
from psydac.fem.basic import FemField
from psydac.fem.vector import VectorFemSpace
from psydac.fem.tensor import TensorFemSpace
from psydac.linalg.block import BlockVector
from psydac.linalg.utilities import array_to_psydac
from psydac.linalg.stencil import StencilVector

from scipy.sparse._lil import lil_matrix
from scipy.sparse._coo import coo_matrix

from sympde.calculus      import grad, dot
from sympde.expr import BilinearForm, LinearForm, integral
from sympde.expr.equation import find, EssentialBC
import sympde.topology as top
from sympde.utilities.utils import plot_domain

from abc import ABCMeta, abstractmethod
import numpy as np
import scipy

from psydac.cad.geometry          import Geometry
from psydac.core.bsplines         import quadrature_grid
from psydac.fem.basic             import FemField
from psydac.fem.tensor import TensorFemSpace
from psydac.fem.vector import VectorFemSpace
from psydac.linalg.kron           import KroneckerLinearSolver
from psydac.linalg.block          import BlockDiagonalSolver
from psydac.utilities.quadratures import gauss_legendre

from sympde.topology.domain       import Domain

from scipy.sparse import bmat
from scipy.sparse._lil import lil_matrix
from scipy.sparse.linalg import eigs, spsolve
from scipy.sparse.linalg import inv

from psydac.fem.tests.get_integration_function import solve_poisson_2d_annulus

def _create_domain_and_derham() -> Tuple[Domain, Derham]:
    """ Creates domain and de Rham sequence on annulus with rmin=1. and rmax=2."""
    logical_domain = Square(name='logical_domain', bounds1=(0,1), bounds2=(0,2*np.pi))
    boundary_logical_domain = Union(logical_domain.get_boundary(axis=0, ext=-1),
                                    logical_domain.get_boundary(axis=0, ext=1))
    logical_domain = Domain(name='logical_domain',
                            interiors=logical_domain.interior,
                            boundaries=boundary_logical_domain,
                            dim=2)
    polar_mapping = PolarMapping(name='polar_mapping', dim=2, c1=0., c2=0.,
                                 rmin=1.0, rmax=2.0)
    annulus = polar_mapping(logical_domain)
    derham = Derham(domain=annulus, sequence=['H1', 'Hdiv', 'L2'])
    return annulus, derham


def test_magnetostatic_pbm_homogeneous():
    """ Test the magnetostatic problem with homogeneous right hand side and 
    curve integral zero"""
    annulus, derham = _create_domain_and_derham()
    ncells = [10,10]
    annulus_h = discretize(annulus, ncells=ncells, periodic=[False, True])
    derham_h = discretize(derham, annulus_h, degree=[2,2])
    assert isinstance(derham_h, DiscreteDerham)

    f = sympy.Tuple(1e-10, 1e-10)
    J = sympy.sympify('1e-10')
    x,y = sympy.symbols(names='x y')

    # Compute the integration function psi and compute right hand side 
    # of the curve integral
    sigma, tau = top.elements_of(derham.V0, names='sigma tau')
    inner_prod_J = LinearForm(tau, integral(annulus, J*tau))
    inner_prod_J_h = discretize(inner_prod_J, annulus_h, space=derham_h.V0)
    assert isinstance(inner_prod_J_h, DiscreteLinearForm)
    inner_prod_J_h_vec = inner_prod_J_h.assemble().toarray()
    assert isinstance(inner_prod_J_h_vec, np.ndarray)
    c_0 = 0.
    boundary_values_poisson = 1/3*(x**2 + y**2 - 1)  # Equals one 
        # on the exterior boundary and zero on the interior boundary
    psi_h = solve_poisson_2d_annulus(annulus_h, derham_h.V0, rhs=1e-10, 
                                     boundary_values=boundary_values_poisson)
    psi_h_coeffs = psi_h.coeffs.toarray()
    curve_integral_rhs = c_0 + np.dot(inner_prod_J_h_vec, psi_h_coeffs)
    
    B = solve_magnetostatic_pbm_annulus(f, psi_h, rhs_curve_integral=curve_integral_rhs,
                                        derham_h=derham_h,
                                        derham=derham,
                                        annulus_h=annulus_h)
    assert isinstance(B, np.ndarray)
    logger = logging.getLogger(name='test_homogeneous')
    logger.debug('B.max():%s',B.max())
    logger.debug('B.min():%s\n',B.min())
    assert np.linalg.norm(B) < 1e-6, f"np.linalg.norm(B):{np.linalg.norm(B)}"

def test_magnetostatic_pbm_manufactured():
    """ Test magnetostatic problem with curve integral on the outer boundary"""
    logger = logging.getLogger(name='test_magnetostatic')
    annulus, derham = _create_domain_and_derham()
    ncells = [10,10]
    annulus_h = discretize(annulus, ncells=ncells, periodic=[False, True])
    derham_h = discretize(derham, annulus_h, degree=[2,2])
    assert isinstance(derham_h, DiscreteDerham)

    # Compute right hand side
    x,y = sympy.symbols(names='x y')
    boundary_values_poisson = 1/3*(x**2 + y**2 - 1)  # Equals one 
        # on the exterior boundary and zero on the interior boundary
    psi_h = solve_poisson_2d_annulus(annulus_h, derham_h.V0, rhs=1e-10, 
                                     boundary_values=boundary_values_poisson)

    J = 4*x**2 - 12*x**2/sympy.sqrt(x**2 + y**2) + 4*y**2 - 12*y**2/sympy.sqrt(x**2 + y**2) + 8
    f = sympy.Tuple(8*y - 12*y/sympy.sqrt(x**2 + y**2), -8*x + 12*x/sympy.sqrt(x**2 + y**2))
    sigma, tau = top.elements_of(derham.V0, names='sigma tau')
    inner_prod_J = LinearForm(tau, integral(annulus, J*tau))
    inner_prod_J_h = discretize(inner_prod_J, annulus_h, space=derham_h.V0)
    assert isinstance(inner_prod_J_h, DiscreteLinearForm)
    inner_prod_J_h_stencil = inner_prod_J_h.assemble()
    # Try changing this to the evaluation using the dicrete linear form directly
    assert isinstance(inner_prod_J_h_stencil, StencilVector)
    inner_prod_J_h_vec = inner_prod_J_h_stencil.toarray()
    psi_h_coeffs = psi_h.coeffs.toarray()
    c_0 = 0.
    curve_integral_rhs = c_0 + np.dot(inner_prod_J_h_vec, psi_h_coeffs)

    B_h_coeffs_arr = solve_magnetostatic_pbm_annulus(f, psi_h, rhs_curve_integral=curve_integral_rhs,
                                                     derham_h=derham_h,
                                                     derham=derham,
                                                     annulus_h=annulus_h)
    B_h_coeffs = array_to_psydac(B_h_coeffs_arr, derham_h.V1.vector_space)
    B_h = FemField(derham_h.V1, coeffs=B_h_coeffs)

    does_plot = False
    if does_plot:
        output_manager = OutputManager('spaces_magnetostatic.yml', 
                                       'fields_magnetostatic.h5')
        output_manager.add_spaces(V1=derham_h.V1)
        output_manager.export_space_info()
        output_manager.set_static()
        output_manager.export_fields(B_h=B_h)
        post_processor = PostProcessManager(domain=annulus, 
                                            space_file='spaces_magnetostatic.yml',
                                            fields_file='fields_magnetostatic.h5')
        post_processor.export_to_vtk('magnetostatic_pbm_vtk', npts_per_cell=3,
                                        fields=("B_h"))
    
    eval_grid = [np.array([0.25, 0.5, 0.75]), np.array([np.pi/2, np.pi])]
    V1h = derham_h.V1
    assert isinstance(V1h, VectorFemSpace)
    B_h_eval = V1h.eval_fields(eval_grid, B_h)
    print(B_h_eval)
    assert np.linalg.norm(B_h_eval[0][0]) < 1e-5
    assert abs( B_h_eval[0][1][0,1] - (0.25-1)**2 * (0.25+1)) < 0.01
    assert abs( B_h_eval[0][1][1,0] - (0.5-1)**2 * (0.5+1)) < 0.01
    assert abs( B_h_eval[0][1][2,1] - (0.75-1)**2 * (0.75+1)) < 0.01

def test_magnetostatic_pbm_inner_curve():
    """Test with curve gamma on r = 1.5"""
    annulus, derham = _create_domain_and_derham()

    ncells = [10,10]
    annulus_h = discretize(annulus, ncells=ncells, periodic=[False, True])
    derham_h = discretize(derham, annulus_h, degree=[2,2])
    assert isinstance(derham_h, DiscreteDerham)

    psi = lambda alpha, theta : 2*alpha if alpha <= 0.5 else 1.0
    h1_proj = Projector_H1(derham_h.V0)
    psi_h = h1_proj(psi) 
    x, y = sympy.symbols(names='x, y')
    J = 4*x**2 - 12*x**2/sympy.sqrt(x**2 + y**2) + 4*y**2 - 12*y**2/sympy.sqrt(x**2 + y**2) + 8
    f = sympy.Tuple(8*y - 12*y/sympy.sqrt(x**2 + y**2), -8*x + 12*x/sympy.sqrt(x**2 + y**2))
    
    # Compute right hand side of the curve integral constraint
    logical_domain_gamma = Square(name='logical_domain_gamma', bounds1=(0,0.5), bounds2=(0,2*np.pi))
    boundary_logical_domain_gamma = Union(logical_domain_gamma.get_boundary(axis=0, ext=-1),
                                    logical_domain_gamma.get_boundary(axis=0, ext=1))
    logical_domain_gamma = Domain(name='logical_domain_gamma',
                            interiors=logical_domain_gamma.interior,
                            boundaries=boundary_logical_domain_gamma,
                            dim=2)
    polar_mapping = PolarMapping(name='polar_mapping', dim=2, c1=0., c2=0.,
                                 rmin=1.0, rmax=2.0)
    omega_gamma = polar_mapping(logical_domain_gamma)
    derham_gamma = Derham(domain=omega_gamma, sequence=['H1', 'Hdiv', 'L2'])
    omega_gamma_h = discretize(omega_gamma, ncells=[5,10], periodic=[False, True])
    derham_gamma_h = discretize(derham_gamma, omega_gamma_h, degree=[2,2])
    h1_proj_gamma = Projector_H1(derham_gamma_h.V0)
    assert isinstance(derham_h, DiscreteDerham)
    sigma, tau = top.elements_of(derham_gamma.V0, names='sigma tau')
    inner_prod_J = LinearForm(tau, integral(omega_gamma, J*tau))
    inner_prod_J_h = discretize(inner_prod_J, omega_gamma_h, space=derham_gamma_h.V0)
    assert isinstance(inner_prod_J_h, DiscreteLinearForm)
    inner_prod_J_h_stencil = inner_prod_J_h.assemble()
    # Try changing this to the evaluation using the dicrete linear form directly
    assert isinstance(inner_prod_J_h_stencil, StencilVector)
    inner_prod_J_h_vec = inner_prod_J_h_stencil.toarray()
    psi_h_gamma = h1_proj_gamma(psi)
    psi_h_gamma_coeffs = psi_h_gamma.coeffs.toarray()
    c_0 = -1.125*np.pi
    rhs_curve_integral = c_0 + np.dot(inner_prod_J_h_vec, psi_h_gamma_coeffs)

    does_plot_psi = False
    if does_plot_psi:
        output_manager_omega = OutputManager('magnetostatic_V0.yml',
                                             'psi_h.h5')
        output_manager_omega.add_spaces(V0=derham_h.V0)
        output_manager_omega.export_space_info()
        output_manager_omega.set_static()
        output_manager_omega.export_fields(psi_h=psi_h)
        post_processor = PostProcessManager(domain=annulus,
                                            space_file='magnetostatic_V0.yml',
                                            fields_file='psi_h.h5')
        post_processor.export_to_vtk('psi_h_vtk', npts_per_cell=5, fields='psi_h')


    B_h_coeffs_arr = solve_magnetostatic_pbm_annulus(f=f, psi_h=psi_h, rhs_curve_integral=rhs_curve_integral,
                                                     derham=derham,
                                                     derham_h=derham_h,
                                                     annulus_h=annulus_h)

    B_h_coeffs = array_to_psydac(B_h_coeffs_arr, derham_h.V1.vector_space)
    B_h = FemField(derham_h.V1, coeffs=B_h_coeffs)

    does_plot = False
    if does_plot:
        output_manager = OutputManager('spaces_magnetostatic.yml', 
                                       'fields_magnetostatic.h5')
        output_manager.add_spaces(V1=derham_h.V1)
        output_manager.export_space_info()
        output_manager.set_static()
        output_manager.export_fields(B_h=B_h)
        post_processor = PostProcessManager(domain=annulus, 
                                            space_file='spaces_magnetostatic.yml',
                                            fields_file='fields_magnetostatic.h5')
        post_processor.export_to_vtk('magnetostatic_pbm_vtk', npts_per_cell=3,
                                        fields=("B_h"))
    
    does_plot_psi_omega = False
    if does_plot_psi_omega:
        output_manager_gamma = OutputManager('V0_gamma.yml', 'psi_h_gamma.h5')
        output_manager_gamma.add_spaces(V0_gamma=derham_gamma_h.V0)
        output_manager_gamma.export_space_info()
        output_manager_gamma.set_static()
        output_manager_gamma.export_fields(psi_h_gamma=psi_h_gamma)
        post_processor_gamma = PostProcessManager(domain=omega_gamma,
                                                  space_file='V0_gamma.yml',
                                                  fields_file='psi_h_gamma.h5')
        post_processor_gamma.export_to_vtk('psi_h_gamma_vtk', npts_per_cell=5,
                                           fields=('psi_h_gamma'))
    
    checks_values_near_omega = False
    if checks_values_near_omega:
        eval_grid_omega = [np.array([0.45,0.475,0.49]), np.array([np.pi/2])]
        psi_h_eval = derham_h.V0.eval_fields(eval_grid_omega, psi_h)
        psi_h_omega_eval = derham_gamma_h.V0.eval_fields(eval_grid_omega, psi_h_gamma)
        print('psi_h_eval:', psi_h_eval)
        print('psi_h_omega_eval:', psi_h_omega_eval)

    eval_grid = [np.array([0.25, 0.5, 0.75]), np.array([np.pi/2, np.pi])]
    V1h = derham_h.V1
    assert isinstance(V1h, VectorFemSpace)
    B_h_eval = V1h.eval_fields(eval_grid, B_h)
    print(B_h_eval)
    assert np.linalg.norm(B_h_eval[0][0]) < 1e-5
    assert abs( B_h_eval[0][1][0,1] - (0.25-1)**2 * (0.25+1)) < 0.01
    assert abs( B_h_eval[0][1][1,0] - (0.5-1)**2 * (0.5+1)) < 0.01
    assert abs( B_h_eval[0][1][2,1] - (0.75-1)**2 * (0.75+1)) < 0.01




if __name__ == '__main__':
    logging.basicConfig(filename='mydebug.log', level=logging.DEBUG, filemode='w')
    # test_magnetostatic_pbm_homogeneous()
    # test_magnetostatic_pbm_manufactured()
    test_magnetostatic_pbm_inner_curve()