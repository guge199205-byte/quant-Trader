// Configuration Loader Utility
// Handles loading and processing configuration files

class ConfigLoader {
    constructor() {
        this.config = null;
        this.basePath = './data';
    }

    async loadConfig() {
        try {
            const response = await fetch('data/config.yaml');
            if (!response.ok) {
                throw new Error(`Failed to load config: ${response.status}`);
            }
            
            const yamlText = await response.text();
            this.config = this.parseYAML(yamlText);
            
            console.log('✅ Configuration loaded successfully');
            return this.config;
        } catch (error) {
            console.error('❌ Error loading configuration, using default:', error);
            // Fallback to default configuration
            this.config = this.getDefaultConfig();
            return this.config;
        }
    }

    parseYAML(yamlText) {
        // Simple YAML parser for our specific use case
        const lines = yamlText.split('\n');
        const config = { markets: {} };
        let currentSection = null;
        let currentMarket = null;
        let indentStack = [];

        for (let i = 0; i < lines.length; i++) {
            // 缩进必须从原始行计算，trim 后永远为 0
            const rawLine = lines[i];
            const indent = rawLine.match(/^(\s*)/)[1].length;
            const line = rawLine.trim();
            if (!line || line.startsWith('#')) continue;

            const isKey = line.includes(':');
            
            if (isKey) {
                // 只切第一个冒号，value 可能含 URL（如 api_base: "http://..."）
                const sep = line.indexOf(':');
                const key = line.slice(0, sep).trim();
                const value = line.slice(sep + 1).trim().replace(/^"(.*)"$/, '$1');
                
                // Handle market sections
                if (key === 'markets') {
                    // 分组键：不赋值，防止覆盖初始的 config.markets 对象
                    currentSection = null;
                    indentStack.push({ indent, key });
                    continue;
                }

                if (key === 'us' || key === 'cn' || key === 'hk') {
                    currentMarket = key;
                    config.markets[key] = { agents: [] };
                    currentSection = null;
                    indentStack = [{ indent, key }];
                    continue;
                }
                
                // Handle nested keys
                const currentIndent = indentStack[indentStack.length - 1];
                if (currentIndent && indent > currentIndent.indent) {
                    if (key === 'agents') {
                        currentSection = 'agents';
                    }
                } else {
                    // Pop from stack if we're going back
                    while (indentStack.length > 0 && indentStack[indentStack.length - 1].indent >= indent) {
                        indentStack.pop();
                    }
                    currentSection = null;
                    // 回到顶层时退出市场上下文（api_base 等顶层键不再落入 market）
                    if (indentStack.length === 0) {
                        currentMarket = null;
                    }
                }
                
                indentStack.push({ indent, key });

                // Set values
                if (key === 'agents' || key === '- folder') {
                    // agents 是列表分组键：统一识别（无论缩进分支），不覆盖已初始化的数组
                    currentSection = 'agents';
                }
                if (currentMarket && currentSection === 'agents') {
                    if (key === '- folder') {
                        // New agent entry（YAML 列表项 "- folder: xxx" 解析为单个 key）
                        const agent = this.parseAgentEntry(lines, i);
                        if (agent) {
                            config.markets[currentMarket].agents.push(agent);
                        }
                    }
                } else if (currentMarket) {
                    config.markets[currentMarket][key] = value || true;
                } else {
                    config[key] = value || true;
                }
            }
        }

        return config;
    }

    parseAgentEntry(lines, startIndex) {
        const agent = {};
        // 提取 "- folder: xxx" 的 folder 值作为条目标识
        const firstLine = lines[startIndex].trim();
        const firstSep = firstLine.indexOf(':');
        if (firstSep > 0 && firstLine.slice(0, firstSep).trim() === '- folder') {
            agent.folder = firstLine.slice(firstSep + 1).trim().replace(/^"(.*)"$/, '$1');
        }
        let i = startIndex + 1;
        
        while (i < lines.length) {
            const rawLine = lines[i];
            const indent = rawLine.match(/^(\s*)/)[1].length;
            const line = rawLine.trim();
            if (!line) break;

            if (indent === 0) break;

            if (line.includes(':')) {
                const sep = line.indexOf(':');
                const key = line.slice(0, sep).trim();
                // 下一个 agent 列表项：停止当前条目解析
                if (key === '- folder') break;
                const value = line.slice(sep + 1).trim().replace(/^"(.*)"$/, '$1');
                agent[key] = value;
            }
            i++;
        }
        
        return Object.keys(agent).length > 0 ? agent : null;
    }

    getDefaultConfig() {
        return {
            markets: {
                us: {
                    name: "US Market (Nasdaq-100)",
                    subtitle: "Track how different AI models perform in Nasdaq-100 stock trading",
                    data_dir: "agent_data",
                    benchmark_file: "Adaily_prices_QQQ.json",
                    benchmark_name: "QQQ",
                    benchmark_display_name: "QQQ Invesco",
                    currency: "USD",
                    icon: "🇺🇸",
                    price_data_type: "individual",
                    time_granularity: "hourly",
                    enabled: true,
                    agents: [
                        { folder: "deepseek-v4-flash", display_name: "DeepSeek V4 Flash", icon: "./figs/deepseek.svg", color: "#4d6bfe", enabled: true },
                        { folder: "deepseek-v4-pro", display_name: "DeepSeek V4 Pro", icon: "./figs/deepseek.svg", color: "#8b5cf6", enabled: true }
                    ]
                },
                cn: {
                    name: "A-Shares (SSE 50)",
                    subtitle: "Track how different AI models perform in SSE 50 A-share stock trading",
                    data_dir: "agent_data_astock",
                    benchmark_file: "A_stock/index_daily_sse_50.json",
                    benchmark_name: "SSE 50",
                    benchmark_display_name: "SSE 50 Index",
                    currency: "CNY",
                    icon: "🇨🇳",
                    price_data_type: "merged",
                    time_granularity: "daily",
                    enabled: true,
                    agents: [
                        { folder: "deepseek-v4-flash", display_name: "DeepSeek V4 Flash", icon: "./figs/deepseek.svg", color: "#4d6bfe", enabled: true },
                        { folder: "deepseek-v4-pro", display_name: "DeepSeek V4 Pro", icon: "./figs/deepseek.svg", color: "#8b5cf6", enabled: true }
                    ]
                }
            },
            data: {
                base_path: "./data",
                price_file_prefix: "daily_prices_",
                benchmark_file: "Adaily_prices_QQQ.json"
            },
            benchmark: {
                folder: "QQQ",
                display_name: "QQQ Invesco",
                icon: "./figs/stock.svg",
                color: "#ff6b00",
                enabled: true
            },
            chart: {
                default_scale: "linear",
                max_ticks: 15,
                point_radius: 0,
                point_hover_radius: 7,
                border_width: 3,
                tension: 0.42
            },
            ui: {
                initial_value: 10000,
                max_recent_trades: 20,
                date_formats: {
                    hourly: "MM/DD HH:mm",
                    daily: "YYYY-MM-DD"
                }
            }
        };
    }

    getMarketConfig(market) {
        if (!this.config) {
            console.warn('Config not loaded yet');
            return null;
        }
        return this.config.markets[market] || null;
    }

    getEnabledAgents(market) {
        const marketConfig = this.getMarketConfig(market);
        if (!marketConfig) return [];

        // 未显式设置 enabled 的 agent 默认启用（兼容旧配置）
        return marketConfig.agents.filter(agent =>
            agent.enabled !== 'false' && agent.enabled !== false
        );
    }

    getDisplayName(agentName, market) {
        const marketConfig = this.getMarketConfig(market);
        if (!marketConfig) return null;
        
        const agent = marketConfig.agents.find(a => a.folder === agentName);
        return agent ? agent.display_name : null;
    }

    getIcon(agentName, market) {
        const marketConfig = this.getMarketConfig(market);
        if (!marketConfig) return null;
        
        const agent = marketConfig.agents.find(a => a.folder === agentName);
        return agent ? agent.icon : null;
    }

    getColor(agentName, market) {
        const marketConfig = this.getMarketConfig(market);
        if (!marketConfig) return null;
        
        const agent = marketConfig.agents.find(a => a.folder === agentName);
        return agent ? agent.color : null;
    }

    getApiToken() {
        return this.config?.api_token || '';
    }

    getDataPath() {
        if (this.config?.api_base) {
            return `${this.config.api_base}/api/data`;
        }
        return this.config?.data?.base_path || './data';
    }

    getPriceFilePrefix() {
        return this.config?.data?.price_file_prefix || 'daily_prices_';
    }

    getBenchmarkFile() {
        return this.config?.data?.benchmark_file || 'Adaily_prices_QQQ.json';
    }

    getUIConfig() {
        return this.config?.ui || { initial_value: 10000 };
    }
}

// Export for use in other modules
window.ConfigLoader = ConfigLoader;