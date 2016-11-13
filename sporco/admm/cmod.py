#-*- coding: utf-8 -*-
# Copyright (C) 2015-2016 by Brendt Wohlberg <brendt@ieee.org>
# All rights reserved. BSD 3-clause License.
# This file is part of the SPORCO package. Details of the copyright
# and user license can be found in the 'LICENSE.txt' file distributed
# with the package.

"""ADMM algorithm for the CMOD problem"""

from __future__ import division
from __future__ import absolute_import

import numpy as np
from scipy import linalg
import copy

from sporco.admm import admm
import sporco.linalg as sl

__author__ = """Brendt Wohlberg <brendt@ieee.org>"""


class CnstrMOD(admm.ADMMEqual):
    """ADMM algorithm for a constrained variant of the Method of Optimal
    Directions (MOD) :cite:`engan-1999-method` problem, referred to here
    as Constrained MOD (CMOD).

    Solve the optimisation problem

    .. math::
       \mathrm{argmin}_D \| D X - S \|_2^2 \quad \\text{such that}
       \quad \| \mathbf{d}_m \|_2 = 1 \;\;,

    where :math:`\mathbf{d}_m` is column :math:`m` of matrix :math:`D`,
    via the ADMM problem

    .. math::
       \mathrm{argmin}_D \| D X - S \|_2^2 + \iota_C(G) \quad
       \\text{such that} \quad D = G \;\;,

    where :math:`\iota_C(\cdot)` is the indicator function of feasible
    set :math:`C` consisting of matrices with unit-norm columns.

    After termination of the :meth:`solve` method, attribute :attr:`itstat` is
    a list of tuples representing statistics of each iteration. The
    fields of the named tuple ``IterationStats`` are:

       ``Iter`` : Iteration number

       ``DFid`` :  Value of data fidelity term :math:`(1/2) \|  D X - S \|_2^2`

       ``Cnstr`` : Constraint violation measure

       ``PrimalRsdl`` : Norm of primal residual

       ``DualRsdl`` : Norm of dual residual

       ``EpsPrimal`` : Primal residual stopping tolerance \
       :math:`\epsilon_{\mathrm{pri}}`

       ``EpsDual`` : Dual residual stopping tolerance \
       :math:`\epsilon_{\mathrm{dua}}`

       ``Rho`` : Penalty parameter

       ``Time`` : Cumulative run time
    """


    class Options(admm.ADMMEqual.Options):
        """CMOD algorithm options

        Options include all of those defined in
        :class:`sporco.admm.admm.ADMMEqual.Options`, together with
        additional options:

        ``AuxVarObj`` : Flag indicating whether the objective function \
        should be evaluated using variable X  (``False``) or Y (``True``) \
        as its argument

        ``ZeroMean`` : Flag indicating whether the solution dictionary \
        :math:`D` should have zero-mean components
        """

        defaults = copy.deepcopy(admm.ADMMEqual.Options.defaults)
        defaults.update({'AuxVarObj' : True, 'ReturnX' : False,
                        'RelaxParam' : 1.8, 'ZeroMean' : False})
        defaults['AutoRho'].update({'Enabled' : True})


        def __init__(self, opt=None):
            """Initialise CMOD algorithm options object."""

            if opt is None:
                opt = {}
            admm.ADMMEqual.Options.__init__(self, opt)

            if self['AuxVarObj']:
                self['fEvalX'] = False
                self['gEvalY'] = True
            else:
                self['fEvalX'] = True
                self['gEvalY'] = False

            if self['AutoRho','RsdlTarget'] is None:
                self['AutoRho','RsdlTarget'] = 1.0



    itstat_fields_objfn = ('DFid', 'Cnstr')
    hdrtxt_objfn = ('DFid', 'Cnstr')
    hdrval_objfun = {'DFid' : 'DFid', 'Cnstr' : 'Cnstr'}



    def __init__(self, A, S, dsz=None, opt=None):
        """
        Initialise a CnstrMOD object with problem parameters.

        Parameters
        ----------
        A : array_like, shape (M, K)
          Sparse representation coefficient matrix
        S : array_like, shape (N, K)
          Signal vector or matrix
        dsz : tuple
          Dictionary size
        opt : :class:`CnstrMOD.Options` object
          Algorithm options
        """

        if opt is None:
            opt = CnstrMOD.Options()

        Nc = S.shape[0]
        # If A not specified, get dictionary size from dsz
        if A is None:
            Nm = dsz[0]
        else:
            Nm = A.shape[0]
        super(CnstrMOD, self).__init__((Nc,Nm), S.dtype, opt)

        # Set penalty parameter
        self.set_attr('rho', opt['rho'], dval=S.shape[1] / 500.0,
                      dtype=self.dtype)

        self.S = np.asarray(S, dtype=self.dtype)

        # Create constraint set projection function
        self.Pcn = getPcn(opt['ZeroMean'])

        if A is not None:
            self.setcoef(A)

        # Increment `runtime` to reflect object initialisation
        # time. The timer object is reset to avoid double-counting of
        # elapsed time if a similar increment is applied in a derived
        # class __init__.
        self.runtime += self.timer.elapsed(reset=True)



    def uinit(self, ushape):
        """Return initialiser for working variable U"""

        if  self.opt['Y0'] is None:
            return np.zeros(ushape, dtype=self.dtype)
        else:
            # If initial Y is non-zero, initial U is chosen so that
            # the relevant dual optimality criterion (see (3.10) in
            # boyd-2010-distributed) is satisfied.
            return self.Y



    def setcoef(self, A):
        """Set coefficient array."""

        self.A = np.asarray(A, dtype=self.dtype)
        self.SAT = self.S.dot(A.T)
        # Factorise dictionary for efficient solves
        self.lu, self.piv = sl.lu_factor(A, self.rho)
        self.lu = np.asarray(self.lu, dtype=self.dtype)



    def getdict(self):
        """Get final dictionary."""

        return self.Y



    def xstep(self):
        """Minimise Augmented Lagrangian with respect to x."""

        self.X = np.asarray(sl.lu_solve_AATI(self.A, self.rho, self.SAT +
                            self.rho*(self.Y - self.U), self.lu, self.piv,),
                            dtype=self.dtype)



    def ystep(self):
        """Minimise Augmented Lagrangian with respect to y."""

        self.Y = self.Pcn(self.AX + self.U)



    def eval_objfn(self):
        """Compute components of objective function as well as total
        contribution to objective function.
        """

        dfd = self.obfn_dfd()
        cns = self.obfn_cns()
        return (dfd, cns)



    def obfn_dfd(self):
        """Compute data fidelity term :math:`(1/2) \| D \mathbf{x} -
        \mathbf{s} \|_2^2`.
        """

        return 0.5*linalg.norm((self.obfn_fvar().dot(self.A) - self.S))**2



    def obfn_cns(self):
        """Compute constraint violation measure :math:`\| P(\mathbf{y}) -
        \mathbf{y}\|_2`.
        """

        return linalg.norm((self.Pcn(self.obfn_gvar()) - self.obfn_gvar()))



    def rhochange(self):
        """Re-factorise matrix when rho changes"""

        self.lu, self.piv = sl.lu_factor(self.A, self.rho)
        self.lu = np.asarray(self.lu, dtype=self.dtype)



def getPcn(zm):
    """Construct constraint set projection function.

    Parameters
    ----------
    zm : bool
      Flag indicating whether the projection function should include
      column mean subtraction

    Returns
    -------
    fn : function
      Constraint set projection function
    """

    if zm:
        return lambda x: normalise(zeromean(x))
    else:
        return normalise



def zeromean(v):
    """Subtract mean of each column of matrix.

    Parameters
    ----------
    v : array_like
      Input dictionary array

    Returns
    -------
    vz : ndarray
      Dictionary array with column means subtracted
    """

    return v - np.mean(v, 0)



def normalise(v):
    """Normalise columns of matrix.

    Parameters
    ----------
    v : array_like
      Array with columns to be normalised

    Returns
    -------
    vnrm : ndarray
      Normalised array
    """

    vn = np.sqrt(np.sum(v**2, 0))
    vn[vn == 0] = 1.0
    return np.asarray(v / vn, dtype=v.dtype)
