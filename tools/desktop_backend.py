"""Frozen desktop backend entry point; binds only to loopback."""
import multiprocessing
import os


if __name__ == '__main__':
    multiprocessing.freeze_support()
    import uvicorn
    from backend.main import app
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('GENTACTIX_PORT', '8700')))
