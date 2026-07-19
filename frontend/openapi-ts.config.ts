import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "../openapi/imtegrale.openapi.json",
  output: {
    path: "src/generated/api",
    clean: true,
  },
});
