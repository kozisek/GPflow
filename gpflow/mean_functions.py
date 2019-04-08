# Copyright 2016 James Hensman, alexggmatthews, PabloLeon, Valentine Svensson
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
r"""
Throughout GPflow, by default, latent functions being modelled with Gaussian
processes are assumed to have zero mean, f ~ GP(0, k(x,x')).

In some cases we may wish to model only the deviation from a fixed function
with a Gaussian process.  For flexibility this fixed function could be both
input dependent and parameterised function, μ(x; θ),
with some unknown parameters θ, resulting in f ~ GP(μ(x;θ), k(x,x')).

The GPflow :class:`MeanFunction <gpflow.mean_functions.MeanFunction>` class
allows this to be done whilst additionally learning parameters of the
parametric function.
"""

from functools import reduce

import tensorflow as tf
import numpy as np

from . import settings
from .params import Parameter
from .params import Parameterized
from .params import ParamList
from .decors import params_as_tensors


class MeanFunction(Parameterized):
    """
    The base mean function class.
    To implement a mean function, write the __call__ method. This takes a
    tensor X and returns a tensor m(X). In accordance with the GPflow
    standard, each row of X represents one datum, and each row of Y is computed
    independently for each row of X.

    MeanFunction classes can have parameters, see the Linear class for an
    example.
    """
    def __call__(self, X):
        raise NotImplementedError(
            'Implement the __call__ method for this mean function')

    def __add__(self, other):
        return Sum([self, other])

    def __mul__(self, other):
        return Product([self, other])


class Linear(MeanFunction):
    """
    y_i = A x_i + b
    """
    def __init__(self, A=None, b=None):
        """
        A is a matrix which maps each element of X to Y, b is an additive
        constant.

        If X has N rows and D columns, and Y is intended to have Q columns,
        then A must be D x Q, b must be a vector of length Q.
        """
        A = np.ones((1, 1)) if A is None else A
        b = np.zeros(1) if b is None else b
        MeanFunction.__init__(self)
        self.A = Parameter(np.atleast_2d(A), dtype=settings.float_type)
        self.b = Parameter(b, dtype=settings.float_type)

    @params_as_tensors
    def __call__(self, X):
        return tf.tensordot(X, self.A, [[-1], [0]]) + self.b


class Identity(Linear):
    """
    y_i = x_i
    """
    def __init__(self, input_dim=None):
        Linear.__init__(self)
        self.input_dim = input_dim

    def __call__(self, X):
        return X

    @property
    def A(self):
        if self.input_dim is None:
            raise ValueError(
                'An input_dim needs to be specified when using the '
                '`Identity` mean function in combination with expectations.')

        return tf.eye(self.input_dim, dtype=settings.float_type)

    @property
    def b(self):
        if self.input_dim is None:
            raise ValueError(
                'An input_dim needs to be specified when using the '
                '`Identity` mean function in combination with expectations.')

        return tf.zeros(self.input_dim, dtype=settings.float_type)

    @A.setter
    def A(self, A):
        pass

    @b.setter
    def b(self, b):
        pass


class Constant(MeanFunction):
    """
    y_i = c,,
    """
    def __init__(self, c=None):
        MeanFunction.__init__(self)
        c = np.zeros(1) if c is None else c
        c = np.reshape(c, (1, -1))
        self.c = Parameter(c)

    @params_as_tensors
    def __call__(self, X):
        shape = tf.stack([tf.shape(X)[0], 1])
        return tf.tile(self.c, shape)


class Zero(Constant):
    def __init__(self, output_dim=1):
        Constant.__init__(self)
        self.output_dim = output_dim
        del self.c

    def __call__(self, X):
        shape = tf.concat([tf.shape(X)[:-1], [self.output_dim]], 0)
        return tf.zeros(shape, dtype=settings.float_type)


class SwitchedMeanFunction(MeanFunction):
    """
    This class enables to use different (independent) mean_functions respective
    to the data 'label'.
    We assume the 'label' is stored in the extra column of X.
    """
    def __init__(self, meanfunction_list):
        MeanFunction.__init__(self)
        for m in meanfunction_list:
            assert isinstance(m, MeanFunction)
        self.meanfunction_list = ParamList(meanfunction_list)

    @params_as_tensors
    def __call__(self, X):
        ind = tf.gather(tf.transpose(X), tf.shape(X)[1]-1)  # ind = X[:,-1]
        ind = tf.cast(ind, tf.int32)
        X = tf.transpose(tf.gather(
            tf.transpose(X), tf.range(0, tf.shape(X)[1]-1)))  # X = X[:,:-1]

        # split up X into chunks corresponding to the relevant likelihoods
        x_list = tf.dynamic_partition(X, ind, len(self.meanfunction_list))
        # apply the likelihood-function to each section of the data
        results = [m(x) for x, m in zip(x_list, self.meanfunction_list)]
        # stitch the results back together
        partitions = tf.dynamic_partition(
            tf.range(0, tf.size(ind)), ind, len(self.meanfunction_list))
        return tf.dynamic_stitch(partitions, results)


class Combination(MeanFunction):
    """
    Combine a list of mean functions, e.g. by adding or multiplying
    (see inheriting classes).
    """

    def __init__(self, mean_functions):
        if not all(isinstance(m, MeanFunction) for m in mean_functions):
            raise TypeError('Can only combine MeanFunction instances')
        super().__init__(self)

        # add mean functions to a list, flattening out instances of this class
        mean_function_list = []
        for m in mean_functions:
            if isinstance(m, self.__class__):
                mean_function_list.extend(m.mean_functions)
            else:
                mean_function_list.append(m)
        self.mean_functions = ParamList(mean_function_list)


class Sum(MeanFunction):
    """
    Add multiple mean functions
    """
    def __call__(self, X):
        return reduce(tf.add, [m(X) for m in self.mean_functions])


Additive = Sum  # for compatibility reasons


class Product(MeanFunction):
    """
    Multiply multiple mean functions
    """
    def __call__(self, X):
        return reduce(tf.multiply, [m(X) for m in self.mean_functions])
