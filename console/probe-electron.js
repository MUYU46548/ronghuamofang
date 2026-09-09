const { app } = require('electron');
app.whenReady().then(() => {
  console.log('ELECTRON_READY_OK');
  app.quit();
});
setTimeout(() => { console.log('TIMEOUT_NO_READY'); app.quit(); }, 8000);
