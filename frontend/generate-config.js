#!/usr/bin/env node
/**
 * Generate frontend config from .env file
 * This should be run before building the frontend
 */

import fs from 'fs';
import path from 'path';
import { config } from 'dotenv';

// Load .env file
const __dirname = path.dirname(new URL(import.meta.url).pathname);
const envPath = path.join(__dirname, '..', '.env');

// Check if .env exists, otherwise try .env.example as fallback for local dev
if (fs.existsSync(envPath)) {
    config({ path: envPath });
} else {
    console.warn('⚠️  .env not found, using defaults for development');
}

// Extract relevant frontend config
let subDir = process.env.DEPLOY_SUB_DIR || '';
// normalize: remove any leading/trailing slashes so we can assemble paths predictably
subDir = subDir.replace(/^\/+|\/+$/g, '');
const domain = process.env.PRODUCTION_DOMAIN || 'http://localhost:3000';

// Determine environment: development (local) or production (cgi)
const isDev = process.env.NODE_ENV === 'development';

let API_BASE_PATH, BASE_URL;
if (isDev) {
    // Local Flask dev server, but all routes and API calls are under subDir
    BASE_URL = subDir ? `/${subDir}/` : '/';
    // Proxy API path for dev: e.g. /testing/api -> http://localhost:8000
    API_BASE_PATH = BASE_URL + 'api';
} else {
    // Production CGI endpoint
    API_BASE_PATH = subDir ? `/${subDir}/backend/api.cgi` : '/backend/api.cgi';
    BASE_URL = subDir ? `/${subDir}/` : '/';
}

const frontendConfig = {
    API_BASE_PATH,
    BASE_URL,
};


// Write to frontend/src/config.js
const configContent = `// Auto-generated from .env - DO NOT EDIT MANUALLY
// Run 'npm run generate-config' to regenerate

export const config = ${JSON.stringify(frontendConfig, null, 2)};
`;
const outputPath = path.join(__dirname, 'src', 'config.js');
fs.writeFileSync(outputPath, configContent);

// Generate .htaccess from template
const htaccessTemplatePath = path.join(__dirname, '.htaccess.template');
const htaccessOutPath = path.join(__dirname, '.htaccess');

let errorDocPath = subDir ? `/${subDir}/index.html` : '/index.html';
let rewriteBase = subDir ? `/${subDir}/` : '/';
let htaccessContent = fs.readFileSync(htaccessTemplatePath, 'utf8')
    .replace('__ERROR_DOC_PATH__', errorDocPath)
    .replace('__REWRITE_BASE__', rewriteBase);
fs.writeFileSync(htaccessOutPath, htaccessContent);

console.log('✅ Frontend config generated:', outputPath);
console.log('   API_BASE_PATH:', frontendConfig.API_BASE_PATH);
console.log('   BASE_URL:', frontendConfig.BASE_URL);
console.log('✅ .htaccess generated:', htaccessOutPath);
console.log('   ErrorDocument 404', errorDocPath);
