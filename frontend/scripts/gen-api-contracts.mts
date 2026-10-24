import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HTTP_METHODS = ["get", "post", "put", "patch", "delete"];
const JSON_CONTENT = "application/json";

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const backendDir = path.resolve(frontendDir, "..", "backend");
const openapiFile = path.join(backendDir, "openapi.json");
const contractsFile = path.join(frontendDir, "src", "api", "api-contracts.json");

export interface ContractOperation {
  operationId: string;
  method: string;
  path: string;
  tags: string[];
  request: unknown;
  responses: Record<string, unknown>;
}

export interface ApiContracts {
  operations: ContractOperation[];
  components: { schemas: Record<string, unknown> };
}

type OpenApiOperation = {
  operationId?: string;
  tags?: string[];
  requestBody?: { content?: Record<string, { schema?: unknown }> };
  responses?: Record<string, { content?: Record<string, { schema?: unknown }> }>;
};

type OpenApiSpec = {
  paths?: Record<string, Record<string, OpenApiOperation>>;
  components?: { schemas?: Record<string, unknown> };
};

function jsonSchema(content: Record<string, { schema?: unknown }> | undefined): unknown {
  return content?.[JSON_CONTENT]?.schema ?? null;
}

export function buildContracts(spec: OpenApiSpec): ApiContracts {
  const operations: ContractOperation[] = [];
  for (const [route, methods] of Object.entries(spec.paths ?? {})) {
    for (const method of HTTP_METHODS) {
      const operation = methods[method];
      if (!operation) continue;
      if (!operation.operationId) {
        throw new Error(`${method.toUpperCase()} ${route} 缺少 operationId`);
      }
      const responses: Record<string, unknown> = {};
      for (const [status, response] of Object.entries(operation.responses ?? {})) {
        responses[status] = jsonSchema(response.content);
      }
      operations.push({
        operationId: operation.operationId,
        method: method.toUpperCase(),
        path: route,
        tags: operation.tags ?? [],
        request: jsonSchema(operation.requestBody?.content),
        responses,
      });
    }
  }
  operations.sort((a, b) => a.operationId.localeCompare(b.operationId));

  const operationIds = operations.map((operation) => operation.operationId);
  const duplicated = operationIds.filter((id, index) => operationIds.indexOf(id) !== index);
  if (duplicated.length > 0) {
    throw new Error(`operationId 重复：${[...new Set(duplicated)].join(", ")}`);
  }

  return {
    operations,
    components: { schemas: spec.components?.schemas ?? {} },
  };
}

export function serializeContracts(contracts: ApiContracts): string {
  return `${JSON.stringify(contracts, null, 2)}\n`;
}

function exportOpenApi(): void {
  execFileSync("uv", ["run", "python", "-m", "scripts.export_openapi"], {
    cwd: backendDir,
    stdio: ["ignore", "inherit", "inherit"],
  });
}

function main(): number {
  const check = process.argv.includes("--check");
  exportOpenApi();
  if (!existsSync(openapiFile)) {
    console.error(`未生成 ${openapiFile}`);
    return 1;
  }
  const spec = JSON.parse(readFileSync(openapiFile, "utf-8")) as OpenApiSpec;
  const content = serializeContracts(buildContracts(spec));

  if (check) {
    const current = existsSync(contractsFile) ? readFileSync(contractsFile, "utf-8") : "";
    if (current !== content) {
      console.error("api-contracts.json 与后端接口不一致，请运行 npm run gen:api 并提交结果");
      return 1;
    }
    console.log("api-contracts.json 与后端接口一致");
    return 0;
  }

  writeFileSync(contractsFile, content, "utf-8");
  console.log(`已生成 ${contractsFile}`);
  return 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main();
}
