import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'fs'
import path from 'path'

// Read config for base path from .env
const sharedDir = path.resolve(__dirname, '..', 'shared');
let base = '/';
let apiPrefix = '/api';
try {
  const envPath = path.resolve(__dirname, '..', '.env');
  if (fs.existsSync(envPath)) {
    const envContent = fs.readFileSync(envPath, 'utf8');
    const subDirMatch = envContent.match(/DEPLOY_SUB_DIR=(.*)/);
    const subDir = subDirMatch ? subDirMatch[1].trim().replace(/^\/+/g, '').replace(/\/+$/g, '') : '';
    base = subDir ? `/${subDir}/` : '/';
    apiPrefix = base + 'api';
  } else {
    console.warn('.env not found, using default base path "/"');
  }
} catch (e) {
  console.warn('Could not read .env, using default base path "/"');
}

export default defineConfig({
  plugins: [react()],
  base: base,
  resolve: {
    alias: {
      // Files shared with the backend (e.g. LLM schemas in shared/llm).
      // JSON imports are bundled at build time.
      '@shared': sharedDir,
    },
  },
  server: {
    port: 3000,
    fs: {
      allow: [__dirname, sharedDir],
    },
    proxy: {
      [apiPrefix]: {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => {
          // Remove the base + 'api' prefix (e.g., /testing/api) from the start
          const prefix = apiPrefix.endsWith('/') ? apiPrefix : apiPrefix + '/';
          const regex = new RegExp(`^${prefix}`);
          const rewritten = path.replace(regex, '/');
          // eslint-disable-next-line no-console
          console.log(`[vite-proxy] ${path} -> ${rewritten}`);
          return rewritten;
        },
      },
    },
  },
  build: {
    // Ensure correct base path for production
    outDir: 'dist'
  }
})
