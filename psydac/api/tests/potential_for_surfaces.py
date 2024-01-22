import logging
import numpy as np
from sympy import ImmutableDenseMatrix
from typing import Callable

from sympde.calculus import grad, dot

from sympde.expr     import Norm
from sympde.expr.expr          import LinearForm, BilinearForm
from sympde.expr.expr          import integral              
from sympde.topology import ScalarFunctionSpace, VectorFunctionSpace
from sympde.topology import element_of, elements_of
from sympde.topology.analytical_mapping import PolarMapping
from sympde.topology.domain import Cube
from sympde.topology import Domain
from sympde.topology.mapping import Mapping
from sympde.topology import Derham

from psydac.cad.geometry     import Geometry
from psydac.feec.global_projectors import Projector_H1
from psydac.feec.pushforward import Pushforward
from psydac.feec.pull_push import push_3d_hcurl
from psydac.fem.basic import FemField
from psydac.fem.tensor import TensorFemSpace
from psydac.api.discretization import discretize
from psydac.api.feec import DiscreteDerham
from psydac.linalg.stencil import StencilVector
from psydac.linalg.utilities import array_to_psydac
from psydac.api.fem import DiscreteLinearForm, DiscreteBilinearForm
from psydac.api.postprocessing import OutputManager, PostProcessManager

class CylinderMapping(Mapping):
    """
    Represents the mapping corresponding to cylinder coordinates.

    """
    _expressions = {'x': 'c1 + (rmin*(1-x1)+rmax*x1)*cos(x2)',
                    'y': 'c2 + (rmin*(1-x1)+rmax*x1)*sin(x2)',
                    'z': 'x3'}
    _ldim        = 3
    _pdim        = 3


def find_potential(alpha0 : FemField, beta0 : FemField, B_h : FemField,
                   derham_h : DiscreteDerham, derham, domain, domain_h):
    assert isinstance(domain, Domain)
    logger = logging.getLogger(name="find_potential")
    # log_domain = Cube(name="log_domain", bounds1=(0,1), 
    #                     bounds2=(0,2*np.pi), bounds3=(0,1))
    # cylinder_mapping = CylinderMapping(name="cylinder_mapping", rmin=0, rmax=1, c1=0, c2=0)

    # domain : Domain = cylinder_mapping(log_domain)
    # logger.debug("type(domain):%s", type(domain))
    # logger.debug("domain.dim: %s", domain.dim) 

    ncells = [6,6,6]
    N = derham_h.V0.vector_space.dimension
    # domain_h : Geometry = discretize(domain, ncells=ncells, 
    #                                 periodic=[False, True, False])
    
    # derham = Derham(domain, sequence=['H1', 'Hcurl', 'Hdiv', 'L2'])
    degree = [3, 3, 3]
    # derham_h = discretize(derham, domain_h, degree=degree)
    assert isinstance(derham_h, DiscreteDerham)
    # V0 = ScalarFunctionSpace(name='V0', domain=domain, kind='H1')
    # V1 = VectorFunctionSpace(name='V1', domain=domain, kind='Hcurl')
    alpha, alpha1, beta, beta1 = elements_of(derham.V0, 'alpha, alpha1, beta, beta1')
    B  = element_of(derham.V1, name='B')
    # J = Norm(B - alpha*grad(beta), domain=domain, kind='l2')

    lambda_deriv_summand1_symbolic = LinearForm(beta1, integral(domain, -2*alpha*dot(B, grad(beta1))))
    lambda_deriv_summand2_symbolic = LinearForm( beta1, integral(domain, 2*alpha*alpha*dot(grad(beta), grad(beta1))) )
    lambda_deriv_summand1_discrete = discretize(lambda_deriv_summand1_symbolic, domain_h, derham_h.V0)
    lambda_deriv_summand2_discrete = discretize(lambda_deriv_summand2_symbolic, domain_h, derham_h.V0)
    mu_deriv_expr = integral(domain, alpha1*dot(B - alpha*grad(beta) , grad(beta)))
    mu_deriv_symbolic = LinearForm(alpha1, mu_deriv_expr)
    mu_deriv_discrete = discretize(mu_deriv_symbolic, domain_h, derham_h.V0)
    

    solution_is_found = False
    alpha_h = alpha0
    beta_h = beta0
    beta_coeff = beta_h.coeffs.toarray() # TODO: Name
    alpha_coeff = alpha_h.coeffs.toarray()
    alpha_beta_coeff = np.concatenate((alpha_coeff, beta_coeff))
    assert isinstance(alpha_beta_coeff, np.ndarray)

    tau = 1.0 # step size
    l2_error = compute_l2_error(domain, domain_h, derham, derham_h, B_h, alpha_h, beta_h)
    logger.debug("l2_error_squared before first gradient step:%s", l2_error)

    # assert isinstance(lambda_deriv, np.ndarray)
    # if np.linalg.norm(lambda_deriv ) < 1e-3:
    #     return alpha_h, beta_h

    while True: # Repeat until the gradient is small enough
        lambda_deriv_summand1 = lambda_deriv_summand1_discrete.assemble(B=B_h, alpha=alpha_h).toarray()
        lambda_deriv_summand2 = lambda_deriv_summand2_discrete.assemble(beta=beta_h, alpha=alpha_h).toarray()
        lambda_deriv = lambda_deriv_summand1 + lambda_deriv_summand2
        mu_deriv = mu_deriv_discrete.assemble(B=B_h, beta=beta_h, alpha=alpha_h).toarray()
        gradient = np.concatenate((mu_deriv, lambda_deriv))
        if np.linalg.norm(gradient ) < 1e-3:
            return alpha_h, beta_h

        def compute_l2_error_squared_from_coeffs(alpha_beta_coeffs : np.ndarray):
            alpha_coeffs = alpha_beta_coeffs[:N]
            beta_coeffs = alpha_beta_coeffs[N:]

            alpha_h = FemField(derham_h.V0, coeffs=array_to_psydac(alpha_coeffs, derham_h.V0.vector_space))
            beta_h = FemField(derham_h.V0, coeffs=array_to_psydac(beta_coeffs, derham_h.V0.vector_space))
            return compute_l2_error(domain, domain_h, derham, derham_h, B_h, alpha_h, beta_h)**2
        # ###DEBUG###
        # ### Test gradient w.r.t. lambda ###
        # forward_step = beta_coeff.copy()
        # backward_step = beta_coeff.copy()
        # forward_step[(2,2,2)] += 0.1
        # backward_step[(2,2,2)] -= 0.1
        # logger.debug("%s forward_step[(2,2,2)]:%s", type(forward_step[(2,2,2)]), forward_step[(2,2,2)])
        # value_forward_step = compute_l2_error_squared_from_coeffs(forward_step)
        # value_backward_step = compute_l2_error_squared_from_coeffs(backward_step)
        # deriv_approx = (value_forward_step - value_backward_step) / 0.2
        # logger.debug("deriv_approx:%s", deriv_approx)
        # logger.debug("%s lambda_deriv[(2,2,2)]:%s", type(lambda_deriv[(2,2,2)]), lambda_deriv[(2,2,2)])
        # #############

        tau = step_size(beta=0.3, 
                        sigma=0.01, 
                        x=alpha_beta_coeff, 
                        func=compute_l2_error_squared_from_coeffs,
                        gradient=gradient
                        ) 

        alpha_beta_coeff = alpha_beta_coeff - tau*gradient
        alpha_coeffs = alpha_beta_coeff[:N]
        beta_coeffs = alpha_beta_coeff[N:]
        beta_h = FemField(derham_h.V0, coeffs=array_to_psydac(beta_coeffs, derham_h.V0.vector_space))
        alpha_h = FemField(derham_h.V0, coeffs=array_to_psydac(alpha_coeffs, derham_h.V0.vector_space))

        ##DEBUG##
        # Plot alpha_h
        does_plot = False
        if does_plot:
            does_plot = False
            output_manager = OutputManager('plot_files/spaces_potential.yml', 
                                        'plot_files/fields_potential.h5')
            output_manager.add_spaces(V0=derham_h.V0)
            output_manager.export_space_info()
            output_manager.set_static()
            output_manager.export_fields(alpha_h = alpha_h)
            post_processor = PostProcessManager(domain=domain, 
                                                space_file='plot_files/spaces_potential.yml',
                                                fields_file='plot_files/fields_potential.h5')
            post_processor.export_to_vtk('plot_files/alpha_plot', npts_per_cell=5,
                                            fields=("alpha_h"))
        #########

        l2_error = compute_l2_error(domain, domain_h, derham, derham_h, B_h, alpha_h, beta_h)
        logger.debug("l2_error_squared after gradient step:%s", l2_error**2)
        # lambda_deriv_summand1 = lambda_deriv_summand1_discrete.assemble(B=B_h, alpha=alpha_h)
        # lambda_deriv_summand2 = lambda_deriv_summand2_discrete.assemble(beta=beta_h, alpha=alpha_h)
        # lambda_deriv = lambda_deriv_summand1 + lambda_deriv_summand2
        # assert isinstance(lambda_deriv, StencilVector)



# TODO: What type is gradient?
def step_size(beta, sigma, x, 
              func : Callable[[np.ndarray], float], 
              gradient : StencilVector):
    """
    Compute the step size of the gradient descent step using Armijo's Rule

    Parameters
    ----------
    beta
        The factor of how much the step is decreased
    sigma : 
    func:
        The function to be minimized
    gradient:
        The gradient of the minimized function 

    References
    ----------
    https://cmazzaanthony.github.io/coptim/gradient_method.html    
    """
    logger = logging.getLogger(name="step_size")
    i = 0
    func_x = func(x)
    inequality_satisfied = False
    logger.debug("i:%s", beta)
    if (func(x - beta**i * gradient) <= func_x - beta**i* sigma * np.linalg.norm(gradient)**2):
        inequality_satisfied = True
    
    while not inequality_satisfied:
        i += 1
        logger.debug("i:%s", i)
        logger.debug("np.linalg.norm(gradient.toarray())**2:%s", np.linalg.norm(gradient)**2)
        func_val_at_new_point = func(x - beta**i * gradient)

        if (func_val_at_new_point <= func_x - beta**i * sigma * np.linalg.norm(gradient)**2):
            inequality_satisfied = True
    return beta**i


def compute_l2_error(domain, domain_h, derham, derham_h, grad_f_h, alpha_h, beta_h):
    logger = logging.getLogger(name="compute_l2_error")
    B, v = elements_of(derham.V1, 'B, v')
    alpha, beta, u = elements_of(derham.V0, 'alpha, beta, u')
    l2_norm_B_sym = Norm(ImmutableDenseMatrix([B[0],B[1], B[2]]), domain, kind='l2')
    l2_norm_B_discrete = discretize(l2_norm_B_sym, domain_h, derham_h.V1)
    l2_norm_B = l2_norm_B_discrete.assemble(B=grad_f_h)
    assert isinstance(l2_norm_B, float)

    B_dot_grad_symbolic = LinearForm(beta, integral(domain, alpha*dot(B,grad(beta))))
    grad_beta_dot_grad_symbolic = LinearForm(u, integral(domain, alpha**2 * dot(grad(beta), grad(u))))
    B_dot_grad_discrete = discretize(
        B_dot_grad_symbolic, domain_h, derham_h.V0)
    assert isinstance(B_dot_grad_discrete, DiscreteLinearForm)
    grad_beta_dot_grad_discrete = discretize(
            grad_beta_dot_grad_symbolic, domain_h, derham_h.V0)
    assert isinstance(grad_beta_dot_grad_discrete, DiscreteLinearForm)
    B_dot_grad = B_dot_grad_discrete.assemble(alpha=alpha_h, B=grad_f_h)
    grad_beta_dot_grad = grad_beta_dot_grad_discrete.assemble(alpha=alpha_h, beta=beta_h)
    # assert isinstance(bilinear_form_B_dot_grad, BlockLinearOperator)
    assert isinstance(B_dot_grad, StencilVector)
    assert isinstance(grad_beta_dot_grad, StencilVector)

    # logger.debug("type(B_dot_grad):%s", type(B_dot_grad))
    # logger.debug("type(bilinear_form_grad_beta_dot_grad):%s", type(grad_beta_dot_grad))
    assert isinstance(derham_h.V0, TensorFemSpace) 

    l2_error_squared = l2_norm_B**2 - 2* B_dot_grad.dot(beta_h.coeffs) + grad_beta_dot_grad.dot(beta_h.coeffs)
    if np.abs(l2_error_squared) < 1.0e-6:
        l2_error_squared = 0.0
    return np.sqrt(l2_error_squared)

