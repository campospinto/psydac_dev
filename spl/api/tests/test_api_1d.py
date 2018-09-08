# -*- coding: UTF-8 -*-

from sympde.core import dx, dy, dz
from sympde.core import Constant
from sympde.core import Field
from sympde.core import grad, dot, inner, cross, rot, curl, div
from sympde.core import FunctionSpace
from sympde.core import TestFunction
from sympde.core import VectorTestFunction
from sympde.core import BilinearForm, LinearForm, FunctionForm

from spl.fem.basic   import FemField
from spl.fem.splines import SplineSpace
from spl.api.discretization import discretize

from numpy import linspace, zeros

def test_api_1d_scalar_1():
    print('============ test_api_1d_scalar_1 =============')

    # ... abstract model
    U = FunctionSpace('U', ldim=1)
    V = FunctionSpace('V', ldim=1)

    v = TestFunction(V, name='v')
    u = TestFunction(U, name='u')

    expr = dot(grad(v), grad(u))

    a = BilinearForm((v,u), expr)
    # ...

    # ... discrete spaces
    # Input data: degree, number of elements
    p  = 3
    ne = 2**4

    # Create uniform grid
    grid = linspace( 0., 1., num=ne+1 )

    # Create finite element space and precompute quadrature data
    V = SplineSpace( p, grid=grid )
    V.init_fem()
    # ...

    # ...
    ah = discretize(a, [V, V])
    M = ah.assemble()
    # ...

def test_api_1d_scalar_2():
    print('============ test_api_1d_scalar_2 =============')

    # ... abstract model
    U = FunctionSpace('U', ldim=1)
    V = FunctionSpace('V', ldim=1)

    v = TestFunction(V, name='v')
    u = TestFunction(U, name='u')

    c = Constant('c', real=True, label='mass stabilization')

    expr = dot(grad(v), grad(u)) + c*v*u

    a = BilinearForm((v,u), expr)
    # ...

    # ... discrete spaces
    # Input data: degree, number of elements
    p  = 3
    ne = 2**4

    # Create uniform grid
    grid = linspace( 0., 1., num=ne+1 )

    # Create finite element space and precompute quadrature data
    V = SplineSpace( p, grid=grid )
    V.init_fem()
    # ...

    # ...
    ah = discretize(a, [V, V])
    M = ah.assemble(0.5)
    # ...

def test_api_1d_scalar_3():
    print('============ test_api_1d_scalar_3 =============')

    # ... abstract model
    U = FunctionSpace('U', ldim=1)
    V = FunctionSpace('V', ldim=1)

    v = TestFunction(V, name='v')
    u = TestFunction(U, name='u')

    F = Field('F', space=V)

    expr = dot(grad(v), grad(u)) + F*v*u

    a = BilinearForm((v,u), expr)
    # ...

    # ... discrete spaces
    # Input data: degree, number of elements
    p  = 3
    ne = 2**4

    # Create uniform grid
    grid = linspace( 0., 1., num=ne+1 )

    # Create finite element space and precompute quadrature data
    V = SplineSpace( p, grid=grid )
    V.init_fem()
    # ...

    # ...
    ah = discretize(a, [V, V])

    # Define a field
    phi = FemField( V, 'phi' )
    phi._coeffs[:,:] = 1.

    M = ah.assemble(phi)
    # ...

def test_api_1d_scalar_4():
    print('============ test_api_1d_scalar_4 =============')

    # ... abstract model
    U = FunctionSpace('U', ldim=1)
    V = FunctionSpace('V', ldim=1)

    v = TestFunction(V, name='v')
    u = TestFunction(U, name='u')

    F = Field('F', space=V)
    G = Field('G', space=V)

    expr = dot(grad(G*v), grad(u)) + F*v*u

    a = BilinearForm((v,u), expr)
    # ...

    # ... discrete spaces
    # Input data: degree, number of elements
    p  = 3
    ne = 2**4

    # Create uniform grid
    grid = linspace( 0., 1., num=ne+1 )

    # Create finite element space and precompute quadrature data
    V = SplineSpace( p, grid=grid )
    V.init_fem()
    # ...

    # ...
    ah = discretize(a, [V, V])

    # Define a field
    phi = FemField( V, 'phi' )
    phi._coeffs[:,:] = 1.

    psi = FemField( V, 'psi' )
    psi._coeffs[:,:] = 1.

    M = ah.assemble(phi, psi)
    # ...

def test_api_1d_block_1():
    print('============ test_api_1d_block_1 =============')

    # ... abstract model
    # 1d wave problem

    U = FunctionSpace('U', ldim=1)
    V = FunctionSpace('V', ldim=1)

    # trial functions
    u = TestFunction(U, name='u')
    f = TestFunction(V, name='f')

    # test functions
    v = TestFunction(U, name='v')
    w = TestFunction(V, name='w')

    rho = Constant('rho', real=True, label='mass density')
    dt = Constant('dt', real=True, label='time step')

    mass = BilinearForm((v,u), v*u)
    adv  = BilinearForm((v,u), dx(v)*u)

    expr = rho*mass(v,u) + dt*adv(v, f) + dt*adv(w,u) + mass(w,f)
    a = BilinearForm(((v,w), (u,f)), expr)
    # ...

    # ... discrete spaces
    # Input data: degree, number of elements
    p  = 3
    ne = 2**4

    # Create uniform grid
    grid = linspace( 0., 1., num=ne+1 )

    # Create finite element space and precompute quadrature data
    V = SplineSpace( p, grid=grid )
    V.init_fem()
    # ...

    # ...
    ah = discretize(a, [V, V])
    M = ah.assemble(0.1, 0.4)
    # ...


###############################################
if __name__ == '__main__':

#    # ... scalar case
#    test_api_1d_scalar_1()
#    test_api_1d_scalar_2()
#    test_api_1d_scalar_3()
#    test_api_1d_scalar_4()
#    # ...

    # ... block case
    test_api_1d_block_1()
    # ...