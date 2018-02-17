# -*- coding: UTF-8 -*-
import numpy as np
import os
from spl.mapping import Mapping

base_dir = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(base_dir, 'data')

if not os.path.isdir(data_dir):
    os.makedirs(data_dir)

def test_cube():
    from caid.cad_geometry import cube
    geometry = cube()
    mapping = Mapping(geometry=geometry)
    mapping.export(os.path.join(data_dir,"cube.nml"))

test_cube()
