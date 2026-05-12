# -*- coding: utf-8 -*-

"""Processing workflow building blocks for the DUSAF 7 algorithm.

This package contains the pure transformations (``pipeline``), the geometry and
attribute QC helpers (``qc``) and the output writers (``output``) that used to
live inside the monolithic algorithm module. Splitting them keeps the algorithm
class small and makes the individual steps reusable from the new wizard UI.
"""
