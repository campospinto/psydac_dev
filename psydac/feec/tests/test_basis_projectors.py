from sympde.topology              import Square
from psydac.feec.multipatch.api   import discretize
from sympde.topology              import Derham
from psydac.feec.basis_projectors import BasisProjectionOperator
from psydac.fem.basic             import FemField

import numpy as np

import pytest

@pytest.mark.parametrize('nc', [4, 8, 15])
@pytest.mark.parametrize('deg', [2,3])
@pytest.mark.parametrize('perio', [[True, True], [True, False], [False, False]])

def test_basis_projector_2d(nc, deg, perio):
    ### INITIALISATION ###
    domain = Square()
    ncells = (nc,nc)
    degree = (deg,deg)
    nquads = [4*(d + 1) for d in degree]
    perio = perio
    domain_h = discretize(domain, ncells=ncells, periodic=perio)

    derham  = Derham(domain, ["H1", "Hdiv", "L2"])
    derham_h = discretize(derham, domain_h, degree=degree, get_vec = True)
    V0h = derham_h.V0
    V1h = derham_h.V1
    V2h = derham_h.V2
    Xh  = derham_h.Vvec

    P0, P1, P2, PX = derham_h.projectors(nquads=nquads)
    #Bunch of (1,1)-periodic function for tests
    f_1 = lambda x, y : x*(x-1)+3
    f_2 = lambda x, y : np.cos(2*np.pi*x)
    f_3 = lambda x, y : np.sin(2*np.pi*x)*y*(y-1)
    f_4 = lambda x, y : x*(x-1)*y*(y-1)
    f_5 = lambda x, y : np.cos(2*np.pi*x)*np.sin(2*np.pi*y)
    f_6 = lambda x, y : x*(x-1)*x*(x-1)+3*y*(y-1)
    f_7 = lambda x, y : np.exp(y)+np.exp(1-y)

    ### TEST V0 -> V1 ###
    fun = [[f_6],[f_5]]
    f_test  = P0(f_3)
    P0_1fv = BasisProjectionOperator(P1, V0h, fun)  
    sol_with_op = P0_1fv.dot(f_test.coeffs)
    sol_no_op  = P1([lambda x, y : f_6(x,y)*f_test(x,y),lambda x, y : f_5(x,y)*f_test(x,y)])
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))
    return
    ### TEST V0->V0 ###
    fun = [[f_1]]
    f_test  = P0(f_2)
    P0_0fv = BasisProjectionOperator(P0, V0h, fun)
    sol_with_op = P0_0fv.dot(f_test.coeffs)
    sol_no_op  = P0(lambda x, y : f_1(x,y)*f_test(x,y))
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

    ### TEST V1 -> V0 ###
    fun = [[f_3,f_4]]
    f_test  = P1([f_1, f_5])
    P1_0fv = BasisProjectionOperator(P0, V1h, fun)  
    sol_with_op = P1_0fv.dot(f_test.coeffs)
    sol_no_op  = P0(lambda x, y : f_3(x,y)*f_test[0](x,y)+f_4(x,y)*f_test[1](x,y))
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

    ### TEST V2 -> V0 ###
    fun = [[f_6]]
    f_test  = P2(f_3)
    P2_0fv = BasisProjectionOperator(P0, V2h, fun)  
    sol_with_op = P2_0fv.dot(f_test.coeffs)
    sol_no_op  = P0(lambda x, y : f_6(x,y)*f_test(x,y))
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

    ### TEST X -> V0 ###
    fun = [[f_4,f_1]]
    f_test  = PX([f_4,f_5])
    PX_0fv = BasisProjectionOperator(P0, Xh, fun)  
    sol_with_op = PX_0fv.dot(f_test.coeffs)
    sol_no_op  = P0(lambda x, y : f_4(x,y)*f_test[0](x,y)+f_1(x,y)*f_test[1](x,y))
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

    ### TEST V1 -> V1 ###
    fun = [[f_2,f_4],[f_5,f_1]]
    f_test  = P1([f_3,f_7])
    P1_1fv = BasisProjectionOperator(P1, V1h, fun)  
    sol_with_op = P1_1fv.dot(f_test.coeffs)
    sol_no_op  = P1([lambda x, y : f_2(x,y)*f_test[0](x,y)+f_4(x,y)*f_test[1](x,y),
                     lambda x, y : f_5(x,y)*f_test[0](x,y)+f_1(x,y)*f_test[1](x,y)])
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

    ### TEST V2 -> V1 ###
    fun = [[f_4],[f_7]]
    f_test  = P2(f_1)
    P2_1fv = BasisProjectionOperator(P1, V2h, fun)  
    sol_with_op = P2_1fv.dot(f_test.coeffs)
    sol_no_op  = P1([lambda x, y : f_4(x,y)*f_test(x,y),lambda x, y : f_7(x,y)*f_test(x,y)])
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

    ### TEST X -> V1 ###
    fun = [[f_3,f_6],[f_5,f_2]]
    f_test  = PX([f_3,f_1])
    PX_1fv = BasisProjectionOperator(P1, Xh, fun)  
    sol_with_op = PX_1fv.dot(f_test.coeffs)
    sol_no_op  = P1([lambda x, y : f_3(x,y)*f_test[0](x,y)+f_6(x,y)*f_test[1](x,y),
                     lambda x, y : f_5(x,y)*f_test[0](x,y)+f_2(x,y)*f_test[1](x,y)])
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

    ### TEST V0->V2 ###
    fun = [[f_4]]
    P0_2fv = BasisProjectionOperator(P2, V0h, fun)
    f_test  = P0(f_2)
    sol_with_op = P0_2fv.dot(f_test.coeffs)
    sol_no_op  = P2(lambda x, y : f_4(x,y)*f_test(x,y))
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

    ### TEST V1 -> V2 ###
    fun = [[f_5,f_2]]
    f_test  = P1([f_1, f_5])
    P1_2fv = BasisProjectionOperator(P2, V1h, fun)  
    sol_with_op = P1_2fv.dot(f_test.coeffs)
    sol_no_op  = P2(lambda x, y : f_5(x,y)*f_test[0](x,y)+f_2(x,y)*f_test[1](x,y))
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

    ### TEST V2 -> V2 ###
    fun = [[f_1]]
    f_test  = P2(f_3)
    P2_2fv = BasisProjectionOperator(P2, V2h, fun)  
    sol_with_op = P2_2fv.dot(f_test.coeffs)
    sol_no_op  = P2(lambda x, y : f_1(x,y)*f_test(x,y))
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

    ### TEST X -> V2 ###
    fun = [[f_3,f_7]]
    f_test  = PX([f_4,f_5])
    PX_2fv = BasisProjectionOperator(P2, Xh, fun)  
    sol_with_op = PX_2fv.dot(f_test.coeffs)
    sol_no_op  = P2(lambda x, y : f_3(x,y)*f_test[0](x,y)+f_7(x,y)*f_test[1](x,y))
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

    ### TEST V0 -> X ###
    fun = [[f_4],[f_1]]
    f_test  = P0(f_2)
    P0_Xfv = BasisProjectionOperator(PX, V0h, fun)  
    sol_with_op = P0_Xfv.dot(f_test.coeffs)
    sol_no_op  = PX([lambda x, y : f_4(x,y)*f_test(x,y),lambda x, y : f_1(x,y)*f_test(x,y)])
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

    ### TEST V1 -> X ###
    fun = [[f_1,f_3],[f_5,f_7]]
    f_test  = P1([f_4,f_5])
    P1_Xfv = BasisProjectionOperator(PX, V1h, fun)  
    sol_with_op = P1_Xfv.dot(f_test.coeffs)
    sol_no_op  = PX([lambda x, y : f_1(x,y)*f_test[0](x,y)+f_3(x,y)*f_test[1](x,y),
                     lambda x, y : f_5(x,y)*f_test[0](x,y)+f_7(x,y)*f_test[1](x,y)])
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

    ### TEST V2 -> X ###
    fun = [[f_2],[f_6]]
    f_test  = P2(f_1)
    P2_Xfv = BasisProjectionOperator(PX, V2h, fun)  
    sol_with_op = P2_Xfv.dot(f_test.coeffs)
    sol_no_op  = PX([lambda x, y : f_2(x,y)*f_test(x,y),lambda x, y : f_6(x,y)*f_test(x,y)])
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

    ### TEST X -> X ###
    fun = [[f_1,f_2],[f_3,f_4]]
    f_test  = PX([f_5,f_6])
    PX_Xfv = BasisProjectionOperator(PX, Xh, fun)  
    sol_with_op = PX_Xfv.dot(f_test.coeffs)
    sol_no_op  = PX([lambda x, y : f_1(x,y)*f_test[0](x,y)+f_2(x,y)*f_test[1](x,y),
                     lambda x, y : f_3(x,y)*f_test[0](x,y)+f_4(x,y)*f_test[1](x,y)])
    print(max(np.abs(sol_with_op.toarray()-sol_no_op.coeffs.toarray()))) 
    #assert(np.allclose(sol_with_op.toarray(),sol_no_op.coeffs.toarray(),1e-12))

if __name__ == '__main__':
    test_basis_projector_2d(4, 2, [True,True])