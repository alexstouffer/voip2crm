.PHONY: setup run dry watch install clean

setup:          ## create venv + install deps + scaffold config
	./setup.sh

install:        ## editable install with transcription extra
	. .venv/bin/activate && pip install -e \x27.[transcribe]\x27

run:            ## process new voicemails once
	. .venv/bin/activate && python run.py --once -v

dry:            ## end-to-end test: local CRM, no transcription
	. .venv/bin/activate && python run.py --once --dry-run --no-transcribe -v

watch:          ## poll continuously (dev only)
	. .venv/bin/activate && python run.py --watch --interval 300

clean:          ## remove caches and build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist *.egg-info
