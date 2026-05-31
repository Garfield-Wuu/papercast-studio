/**
 * Front-end mirror of `papercast.llm.client.PRESETS`.
 *
 * Why mirror instead of fetch from the backend:
 *   - The list rarely changes; treating it as static lets us render
 *     the dropdown synchronously without a query roundtrip on first
 *     paint of SettingsPage.
 *   - Each entry tells the form how to default-fill base_url /
 *     api_key_env / model_examples when the user picks a provider.
 *
 * Keep this in sync with `papercast/llm/client.py:PRESETS`. We
 * intentionally don't auto-derive — the front-end form needs labels
 * in CN and the Python side wants short identifiers; both are stable
 * config that hardly ever changes.
 */
export interface LLMPreset {
  /** Stable key used by the dropdown. */
  key: string;
  /** Human label shown in CN. */
  label: string;
  /** What goes into LLMTarget.provider when this preset is picked. */
  provider: "anthropic" | "openai" | "openai_compat";
  /** Default base_url (null → SDK default). */
  base_url: string | null;
  /** Suggested env var name for the api key. */
  api_key_env: string;
  /** Datalist options for the model field. */
  model_examples: string[];
}

export const LLM_PRESETS: LLMPreset[] = [
  {
    key: "anthropic",
    label: "Anthropic Claude",
    provider: "anthropic",
    base_url: null,
    api_key_env: "ANTHROPIC_API_KEY",
    model_examples: ["claude-sonnet-4-6", "claude-opus-4-8", "claude-haiku-4-5"],
  },
  {
    key: "openai",
    label: "OpenAI",
    provider: "openai",
    base_url: "https://api.openai.com/v1",
    api_key_env: "OPENAI_API_KEY",
    model_examples: ["gpt-5", "gpt-5-mini", "gpt-4.1"],
  },
  {
    key: "deepseek",
    label: "DeepSeek",
    provider: "openai_compat",
    base_url: "https://api.deepseek.com/v1",
    api_key_env: "DEEPSEEK_API_KEY",
    model_examples: ["deepseek-chat", "deepseek-reasoner"],
  },
  {
    key: "moonshot",
    label: "Moonshot Kimi",
    provider: "openai_compat",
    base_url: "https://api.moonshot.cn/v1",
    api_key_env: "MOONSHOT_API_KEY",
    model_examples: ["moonshot-v1-32k", "moonshot-v1-128k"],
  },
  {
    key: "qwen",
    label: "Qwen (DashScope OpenAI 兼容)",
    provider: "openai_compat",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key_env: "DASHSCOPE_API_KEY",
    model_examples: ["qwen-max", "qwen-plus", "qwen-turbo"],
  },
  {
    key: "zhipu",
    label: "智谱 GLM",
    provider: "openai_compat",
    base_url: "https://open.bigmodel.cn/api/paas/v4",
    api_key_env: "ZHIPU_API_KEY",
    model_examples: ["glm-4.6", "glm-4-plus", "glm-4-air"],
  },
  {
    key: "ollama",
    label: "Ollama (本地)",
    provider: "openai_compat",
    base_url: "http://localhost:11434/v1",
    api_key_env: "OLLAMA_API_KEY",
    model_examples: ["qwen3:32b", "llama3.2:latest"],
  },
  {
    key: "vllm",
    label: "vLLM / LM Studio (本地或自托管)",
    provider: "openai_compat",
    base_url: "http://localhost:8000/v1",
    api_key_env: "VLLM_API_KEY",
    model_examples: ["meta-llama/Llama-3.1-70B-Instruct"],
  },
  {
    key: "custom_openai",
    label: "自定义 OpenAI 兼容端点",
    provider: "openai_compat",
    base_url: null,
    api_key_env: "OPENAI_API_KEY",
    model_examples: [],
  },
  {
    key: "custom_anthropic",
    label: "自定义 Anthropic 兼容端点 (Claude 中转)",
    provider: "anthropic",
    base_url: null,
    api_key_env: "ANTHROPIC_API_KEY",
    model_examples: ["claude-sonnet-4-6"],
  },
];

/** Best-effort match of the current LLMTarget to a preset key. */
export function detectPresetKey(args: {
  provider: string;
  base_url: string | null;
  api_key_env: string;
}): string {
  const { provider, base_url, api_key_env } = args;
  for (const p of LLM_PRESETS) {
    if (p.provider !== provider) continue;
    if (p.base_url && base_url && p.base_url === base_url) return p.key;
    if (!p.base_url && !base_url && p.api_key_env === api_key_env) return p.key;
  }
  // Fallback: same provider family, env-var match wins.
  for (const p of LLM_PRESETS) {
    if (p.provider === provider && p.api_key_env === api_key_env) return p.key;
  }
  return "custom_openai";
}

export function getPreset(key: string): LLMPreset | undefined {
  return LLM_PRESETS.find((p) => p.key === key);
}
