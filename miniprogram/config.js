// config.js — 小程序运行配置
//
// DATA_BASE_URL: 静态 JSON 托管地址（部署后改成你的 CDN/GitHub Pages/OSS 域名）
//   最终会请求 {DATA_BASE_URL}/index.json 与 {DATA_BASE_URL}/outline_YYYYMMDD.json
//   上线前需在小程序后台「开发 → 开发管理 → 开发设置 → 服务器域名」把该域名加进 request 合法域名
//
// USE_LOCAL: 开发期开关。true = 直接读取随包打包的 data/local.js（无需 CDN 即可在开发者工具跑通）；
//   拿到真实 CDN 并配置好合法域名后，改成 false 走网络请求。
module.exports = {
  DATA_BASE_URL: 'https://your-cdn.example.com/ad-newsletter',
  USE_LOCAL: true,
};
