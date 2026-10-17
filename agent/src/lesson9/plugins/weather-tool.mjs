// dsh 自定义工具插件：get_weather
// 插件就是一个导出 apply(ctx, config) 的 ES 模块；inject 声明依赖的服务。
// 这里没 import '@deepseek-ai/dsh-tools' 的 defineTool（单文件运行时里本地插件解析不到这个包），
// 直接手写它产出的 ToolDefinition：name / description / parameters(JSON Schema) / output / execute。

export const name = 'demo-weather-tool'
export const inject = ['tools']

const WIND = { N: '北', NNE: '东北偏北', NE: '东北', ENE: '东北偏东', E: '东', ESE: '东南偏东', SE: '东南', SSE: '东南偏南',
  S: '南', SSW: '西南偏南', SW: '西南', WSW: '西南偏西', W: '西', WNW: '西北偏西', NW: '西北', NNW: '西北偏北' }

function desc(item) {
  const zh = item.lang_zh?.[0]?.value
  return zh || item.weatherDesc?.[0]?.value || ''
}

function format(city, data, days) {
  const now = data.current_condition?.[0] ?? {}
  const area = data.nearest_area?.[0]
  const place = area ? `${area.areaName?.[0]?.value ?? city}, ${area.country?.[0]?.value ?? ''}` : city
  const lines = [
    `城市：${place}`,
    `当前：${desc(now)}，${now.temp_C}°C（体感 ${now.FeelsLikeC}°C），湿度 ${now.humidity}%，` +
      `${WIND[now.winddir16Point] ?? now.winddir16Point}风 ${now.windspeedKmph} km/h，观测时间 ${now.localObsDateTime ?? now.observation_time}`,
  ]
  for (const day of (data.weather ?? []).slice(0, days)) {
    const noon = day.hourly?.find(h => h.time === '1200') ?? day.hourly?.[0] ?? {}
    lines.push(`${day.date}：${desc(noon)}，${day.mintempC}~${day.maxtempC}°C，降水概率 ${noon.chanceofrain ?? '?'}%`)
  }
  return lines.join('\n')
}

export function apply(ctx, config = {}) {
  const endpoint = config.endpoint ?? 'https://wttr.in'
  ctx.tools.register({
    name: 'get_weather',
    description: '查询城市的实时天气和未来几天预报（数据来自 wttr.in）。城市名用英文或拼音更稳，例如 Xiamen。',
    parameters: {
      type: 'object',
      properties: {
        city: { type: 'string', description: '城市名，如 Xiamen、Beijing' },
        days: { type: 'integer', description: '预报天数 1-3，默认 3' },
      },
      required: ['city'],
      additionalProperties: false,
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    timeoutMs: config.timeoutMs ?? 20000,
    async execute(args, exec) {
      const city = String(args?.city ?? '').trim()
      if (!city) throw new Error('city 不能为空')
      const days = Math.min(3, Math.max(1, Number(args?.days ?? 3) || 3))
      const url = `${endpoint}/${encodeURIComponent(city)}?format=j1&lang=zh`
      console.error(`[demo-weather-tool] get_weather city=${city} days=${days}`)
      const res = await fetch(url, { signal: exec.signal, headers: { 'User-Agent': 'curl/8' } })
      if (!res.ok) throw new Error(`wttr.in 返回 HTTP ${res.status}`)
      return format(city, await res.json(), days)
    },
  })
}
