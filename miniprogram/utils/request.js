// utils/request.js — 统一数据访问层
// 双模式：USE_LOCAL=true 读随包打包的 data/local.js；否则 wx.request 拉 CDN
const config = require('../config');
const local = require('../data/local');

// 网络 GET 封装（Promise 化 wx.request）
function httpGet(url) {
  return new Promise((resolve, reject) => {
    wx.request({
      url,
      method: 'GET',
      header: { 'Content-Type': 'application/json' },
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          reject({ code: res.statusCode, message: '请求失败' });
        }
      },
      fail: (err) => reject({ code: -1, message: '网络错误', detail: err }),
    });
  });
}

// 获取往期清单 index.json
function getIndex() {
  if (config.USE_LOCAL) {
    return Promise.resolve(local._index);
  }
  return httpGet(`${config.DATA_BASE_URL}/index.json`);
}

// 获取某一期晨报（date 形如 20260708）
function getIssue(date) {
  if (config.USE_LOCAL) {
    const issue = local[date];
    if (issue) return Promise.resolve(issue);
    return Promise.reject({ code: 404, message: '本地无此日期数据' });
  }
  return httpGet(`${config.DATA_BASE_URL}/outline_${date}.json`);
}

module.exports = { getIndex, getIssue };
