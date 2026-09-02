.PHONY: install test clean

install:
	pip install -e .

test:
	python -m pytest tests/

clean:
	rm -rf build/ dist/ *.egg-info/