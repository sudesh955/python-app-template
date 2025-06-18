#!/usr/bin/env python

import debugpy

from exe import main


def debug():
  debugpy.listen(("localhost", 5678))
  print("Waiting for debugger to attach")
  debugpy.wait_for_client()
  main()


if __name__ == "__main__":
  debug()
