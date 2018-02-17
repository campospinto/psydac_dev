# -*- coding: UTF-8 -*-
import numpy as np
import os
from spl.mapping import Mapping

base_dir = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(base_dir, 'data')

def test_square():
    filename = os.path.join(data_dir, 'square.nml')
    mapping = Mapping(filename=filename, p_dim=2)

def test_circle():
    filename = os.path.join(data_dir, 'circle.nml')
    mapping = Mapping(filename=filename, p_dim=2)

test_square()
test_circle()
