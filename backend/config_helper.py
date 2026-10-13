#!/usr/bin/env python3
"""
Utility to read deployment configuration from config.json or environment variables
"""
import json
import os

def get_config():
    """Read and return config from environment variables or config.json"""
    # Priority 1: Environment variables
    if all([
        os.environ.get('DEPLOY_HOST'),
        os.environ.get('DEPLOY_USER'),
        os.environ.get('DEPLOY_BASE_DIR')
    ]):
        return {
            "deployment": {
                "host": os.environ.get('DEPLOY_HOST'),
                "user": os.environ.get('DEPLOY_USER'),
                "baseDir": os.environ.get('DEPLOY_BASE_DIR'),
                "subDir": os.environ.get('DEPLOY_SUB_DIR', ''),
                "backendRoot": os.environ.get('DEPLOY_BACKEND_ROOT', '')
            },
            "production": {
                "domain": os.environ.get('PRODUCTION_DOMAIN', '')
            }
        }
    
    # Priority 2: config.json file
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(os.path.dirname(backend_dir), 'config.json')
    
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        # Fallback to default configuration
        return {
            "deployment": {
                "baseDir": "nb/",
                "subDir": ""
            }
        }

def get_backend_path():
    """Get the full backend path for the server"""
    config = get_config()
    base_dir = config['deployment']['baseDir']
    sub_dir = config['deployment'].get('subDir', '')
    
    if sub_dir:
        return f"/kunden/homepages/29/d557350892/htdocs/{base_dir}{sub_dir}backend"
    else:
        return f"/kunden/homepages/29/d557350892/htdocs/{base_dir}backend"

if __name__ == "__main__":
    print(get_backend_path())
