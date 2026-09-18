!macro customInstall
  # 不弹任何额外对话框。
  # 安装向导完成页自带"启动绒花墨坊"勾选框（MUI_FINISHPAGE_RUN），
  # 这里不再重复询问，避免被当成流氓软件。
!macroend

!macro customUnInstall
  # 卸载时清理用户数据（可选，默认不删）
  # RMDir /r "$LOCALAPPDATA\绒花墨坊"
!macroend
