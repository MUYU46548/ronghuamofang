!macro customInstall
  # 安装完成后询问是否立即运行
  MessageBox MB_YESNO "安装完成！是否立即运行绒花墨坊？" /SD IDNO IDNO noRun
    Exec "$INSTDIR\绒花墨坊.exe"
  noRun:
!macroend

!macro customUnInstall
  # 卸载时清理用户数据（可选，默认不删）
  # RMDir /r "$LOCALAPPDATA\绒花墨坊"
!macroend
