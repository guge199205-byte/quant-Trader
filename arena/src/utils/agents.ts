/** agent 签名的展示名映射（展示层唯一入口）。
 *
 *  签名是目录名/接口参数（`market-research`），所有取数调用必须原样传签名；
 *  只有渲染才换中文。2026-09-12 P2：混合对话流此前直接渲染 `r.name`，
 *  `market-research` 在「全部模型」视图裸露英文——旧的「缺了才补中文卡」
 *  兜底因 overview 早已含该行而永不触发。
 */
const DISPLAY_NAMES: Record<string, string> = {
  'market-research': '市场研究',
};

export function displayAgentName(name: string | null | undefined): string {
  const key = String(name ?? '');
  return DISPLAY_NAMES[key] ?? key;
}
