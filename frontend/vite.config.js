import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'
import { fileURLToPath } from 'url'
import frappeuiPlugin from 'frappe-ui/vite'

const rootDir = path.dirname(fileURLToPath(import.meta.url))

// Chef frontend. Dev server proxies `/api` -> the FastAPI backend on :8000,
// stripping the `/api` prefix so backend routes stay `/recipes`, `/bakes`, ...
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backend = env.CHEF_BACKEND_URL || 'http://localhost:8000'

  return {
    base: '/',
    plugins: [
      frappeuiPlugin({
        lucideIcons: true,
        frappeProxy: false,
        jinjaBootData: false,
        buildConfig: false,
      }),
      vue(),
    ],
    build: {
      // The backend serves `frontend/dist` if present; keep the output there.
      outDir: 'dist',
      emptyOutDir: true,
      sourcemap: mode === 'development',
    },
    resolve: {
      alias: {
        '@': path.resolve(rootDir, 'src'),
        // frappe-ui's markdown util isn't exported; alias the source file directly.
        'frappe-ui/markdown': path.resolve(
          rootDir,
          'node_modules/frappe-ui/src/utils/markdown.ts',
        ),
      },
    },
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: backend,
          changeOrigin: true,
          secure: false,
          rewrite: (p) => p.replace(/^\/api/, ''),
        },
      },
    },
    optimizeDeps: {
      include: ['feather-icons', 'debug'],
      exclude: ['frappe-ui'],
    },
  }
})
