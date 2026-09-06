import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// One process: FastAPI serves what this writes into `dist`, mounted at `/app`.
// `base` must match that mount or every asset URL 404s in production while
// working perfectly in `vite dev` -- which is the kind of difference that only
// shows up after you stop looking.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
});
