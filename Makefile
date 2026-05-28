PROJECT_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

dashboard:
	python3 $(PROJECT_DIR)local/dashboard.py && open $(PROJECT_DIR)local/dashboard.html

monitor:
	python3 $(PROJECT_DIR)monitor.py

.PHONY: dashboard monitor
