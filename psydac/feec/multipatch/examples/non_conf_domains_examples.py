from mpi4py import MPI
import numpy as np
from sympde.topology import Square
from sympde.topology import IdentityMapping, PolarMapping, AffineMapping, Mapping
from sympde.topology  import Boundary, Interface, Union

from scipy.sparse import eye as sparse_eye
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import inv
from scipy.sparse import coo_matrix, bmat
from scipy.sparse.linalg import inv as sp_inv

from psydac.feec.multipatch.utilities import time_count
from psydac.linalg.utilities          import array_to_stencil
from psydac.feec.multipatch.api       import discretize
from psydac.api.settings              import PSYDAC_BACKENDS
from psydac.fem.splines               import SplineSpace

from psydac.feec.multipatch.multipatch_domain_utilities import union, set_interfaces, build_multipatch_domain

def create_square_domain(ncells, interval_x, interval_y, mapping='identity'):

    """
    Create a 2D multipatch square domain with the prescribed number of patch in each direction.

    Parameters
    ----------
    ncells: <matrix>

    |2|
    _____
    |4|2|

    [[2, None],
     [4, 2]]

     [[2, 2, 0, 0],
      [2, 4, 0, 0],
      [4, 8, 4, 2],
      [4, 4, 2, 2]]
     number of patch in each direction

    Returns
    -------
    domain : <Sympde.topology.Domain>
     The symbolic multipatch domain
    """
    ax, bx = interval_x
    ay, by = interval_y 
    nb_patchx, nb_patchy = np.shape(ncells)

    list_Omega = [[Square('OmegaLog_'+str(i)+'_'+str(j),
                    bounds1 = (ax + i/nb_patchx * (bx-ax),ax + (i+1)/nb_patchx * (bx-ax)),
                    bounds2 = (ay + j/nb_patchy * (by-ay),ay + (j+1)/nb_patchy * (by-ay))) for j in range(nb_patchx)] for i in range(nb_patchy)]
    
    
    
    if mapping == 'identity':
        list_mapping = [[IdentityMapping('M_'+str(i)+'_'+str(j),2) for j in range(nb_patchx)] for i in range(nb_patchy)]

    elif mapping == 'polar':
        list_mapping = [[PolarMapping('M_'+str(i)+'_'+str(j),2, c1= 0., c2= 0., rmin = 0., rmax=1.) for j in range(nb_patchx)] for i in range(nb_patchy)]

    list_domain = [[list_mapping[i][j](list_Omega[i][j]) for j in range(nb_patchx)] for i in range(nb_patchy)]
    flat_list = []

    for i in range(nb_patchy):
        for j in range(nb_patchx):
            if ncells[i, j] != None:
                flat_list.append(list_domain[i][j])

    domain = union(flat_list, name='domain')
    interfaces = []

    #interfaces in x
    for j in range(nb_patchx):
        interfaces.extend([[list_domain[i][j].get_boundary(axis=0, ext=+1), list_domain[i+1][j].get_boundary(axis=0, ext=-1), 1] for i in range(nb_patchy-1) if ncells[i][j] != None and ncells[i+1][j] != None])

    #interfaces in y
    for i in range(nb_patchy):
        interfaces.extend([[list_domain[i][j].get_boundary(axis=1, ext=+1), list_domain[i][j+1].get_boundary(axis=1, ext=-1), 1] for j in range(nb_patchx-1) if ncells[i][j] != None and ncells[i][j+1] != None])

    domain = set_interfaces(domain, interfaces)

    return domain

def get_L_shape_ncells(patches, n0):
    ncells = np.zeros((patches, patches), dtype = object)

    pm = int(patches/2)
    assert patches/2 == pm

    for i in range(pm):
        for j in range(pm):
            ncells[i,j] = None

    for i in range(pm, patches):
        for j in range(patches):
            exp = 1+patches - (abs(i-pm)+abs(j-pm))
            ncells[i,j] = n0**exp
            ncells[j,i] = n0**exp

    return ncells