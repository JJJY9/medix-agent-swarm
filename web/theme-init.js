// 在样式加载前确定主题，避免刷新时先闪一下浅色
(() => {
  try {
    const saved = localStorage.getItem('medix.theme');
    const theme =
      saved ||
      (window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light');
    document.documentElement.dataset.theme = theme;
    const themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) {
      themeColor.content = theme === 'dark' ? '#0d1519' : '#0b6e9e';
    }
  } catch {
    document.documentElement.dataset.theme = 'light';
  }
})();
