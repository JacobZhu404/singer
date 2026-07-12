const { getIndex } = require('../../utils/request');

Page({
  data: {
    issues: [],
    loading: true,
  },

  onLoad() {
    this.load();
  },

  onPullDownRefresh() {
    this.load(() => wx.stopPullDownRefresh());
  },

  async load(done) {
    this.setData({ loading: true });
    try {
      const index = await getIndex();
      this.setData({ issues: (index && index.issues) || [], loading: false });
    } catch (e) {
      this.setData({ loading: false });
      wx.showToast({ title: '加载失败', icon: 'none' });
    } finally {
      if (done) done();
    }
  },

  openIssue(e) {
    const date = e.currentTarget.dataset.date;
    wx.navigateTo({ url: `/pages/issue/issue?date=${date}` });
  },
});
