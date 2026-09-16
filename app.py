#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hugging Face Spaces Entrypoint
Book Translation Benchmark & Quality Evaluation
"""

import os
import sys
import functools

# Monkeypatch for Python compatibility
class _HashedSeq(list):
    __slots__ = 'hashvalue'
    def __init__(self, tup, hash=hash):
        self[:] = tup
        self.hashvalue = hash(tup)
    def __hash__(self):
        return self.hashvalue
functools._HashedSeq = _HashedSeq

from app_benchmark_web import main

if __name__ == '__main__':
    main()
