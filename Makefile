pre-commit-init:
	pre-commit install
	pre-commit install --hook-type commit-msg

pre-commit-update:
	pre-commit autoupdate

install:
	python3 -m venv --upgrade venv
	venv/bin/pip3 install -r requirements.txt
