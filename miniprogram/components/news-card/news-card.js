Component({
  properties: {
    item: {
      type: Object,
      value: {},
    },
    num: {
      type: Number,
      value: 0,
    },
  },
  methods: {
    // 点击卡片 → 复制原文链接到剪贴板
    onCopy() {
      const url = this.data.item && this.data.item.url;
      if (!url) {
        wx.showToast({ title: '该条暂无链接', icon: 'none' });
        return;
      }
      wx.setClipboardData({
        data: url,
        success: () => {
          // 新版基础库已不再自动弹系统提示，这里主动给反馈
          wx.showToast({ title: '链接已复制', icon: 'none' });
        },
      });
    },
  },
});
