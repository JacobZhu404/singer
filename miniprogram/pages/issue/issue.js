const { getIndex, getIssue } = require('../../utils/request');

Page({
  data: {
    loading: true,
    issue: null,
    date: '',
  },

  onLoad(options) {
    if (options && options.date) {
      this.loadIssue(options.date);
    } else {
      this.loadLatest();
    }
  },

  // 无 date 参数时，拉 index 取最新一期
  async loadLatest() {
    try {
      const index = await getIndex();
      const latest = index && index.issues && index.issues[0];
      if (!latest) {
        this.setData({ loading: false, issue: null });
        return;
      }
      this.loadIssue(latest.date);
    } catch (e) {
      this.setData({ loading: false });
      wx.showToast({ title: '加载失败', icon: 'none' });
    }
  },

  async loadIssue(date) {
    this.setData({ loading: true, date });
    try {
      const issue = await getIssue(date);
      this.setData({ issue, loading: false });
      wx.setNavigationBarTitle({ title: issue.title || '自动驾驶晨报' });
    } catch (e) {
      this.setData({ loading: false, issue: null });
      wx.showToast({ title: '未找到该期', icon: 'none' });
    }
  },

  onShareAppMessage() {
    const { issue, date } = this.data;
    const shareTitle = issue
      ? `${issue.title} · 今日${issue.total}条自动驾驶动态`
      : '自动驾驶晨报';
    return {
      title: shareTitle,
      path: `/pages/issue/issue?date=${date}`,
    };
  },

  onShareTimeline() {
    const { issue, date } = this.data;
    const shareTitle = issue
      ? `${issue.title} · 今日${issue.total}条自动驾驶动态`
      : '自动驾驶晨报';
    return {
      title: shareTitle,
      query: `date=${date}`,
    };
  },
});
