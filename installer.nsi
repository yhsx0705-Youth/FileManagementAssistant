Unicode true
RequestExecutionLevel user
SetCompressor /SOLID lzma

!include MUI2.nsh
!include nsDialogs.nsh
!include LogicLib.nsh
!include x64.nsh

!ifndef APP_VERSION
  !define APP_VERSION "1.3.9"
!endif
!ifndef APP_STAGE
  !error "APP_STAGE must point to the staged application executable."
!endif
!ifndef OUTPUT_DIR
  !error "OUTPUT_DIR must point to the release directory."
!endif

!define APP_NAME "文件管理辅助器"
!define APP_PUBLISHER "云鹤sx"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\文件管理辅助器"

Name "${APP_NAME} ${APP_VERSION}"
Caption "${APP_NAME} 安装程序"
OutFile "${OUTPUT_DIR}\FileManagementAssistant-Setup-${APP_VERSION}-x64.exe"
InstallDir "$LOCALAPPDATA\Programs\${APP_NAME}"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
ShowInstDetails nevershow
ShowUnInstDetails nevershow
AutoCloseWindow true

!define MUI_ABORTWARNING
!define MUI_ICON "${__FILEDIR__}\quick_access\assets\app.ico"
!define MUI_UNICON "${__FILEDIR__}\quick_access\assets\app.ico"
!define MUI_COMPONENTSPAGE_TEXT_TOP "选择要创建的开始菜单和桌面快捷方式。下一页可设置安装位置。程序安装在当前 Windows 用户下，不需要管理员权限。"
!define MUI_DIRECTORYPAGE_TEXT_TOP "选择文件管理辅助器的安装位置。默认安装到当前用户的本地程序目录。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_NAME}.exe"
!define MUI_FINISHPAGE_RUN_TEXT "立即启动${APP_NAME}"
!define MUI_UNFINISHPAGE_NOAUTOCLOSE

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
UninstPage custom un.UserDataPage un.UserDataPageLeave
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"

Var DataCheckbox
Var RemoveUserData
Var DesktopShortcutCreated
Var StartMenuShortcutCreated

Function un.onInit
  SetShellVarContext current
  StrCpy $RemoveUserData 0
FunctionEnd

Function un.UserDataPage
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 30u "收藏和设置保存在：$\r$\n$LOCALAPPDATA\${APP_NAME}"
  Pop $0
  ${NSD_CreateCheckbox} 0 38u 100% 30u "同时删除已保存的入口和设置（不可恢复）"
  Pop $DataCheckbox
  ${NSD_SetState} $DataCheckbox ${BST_UNCHECKED}
  nsDialogs::Show
FunctionEnd

Function un.UserDataPageLeave
  ${NSD_GetState} $DataCheckbox $RemoveUserData
FunctionEnd

Section "主程序（必需）" SEC_MAIN
  SectionIn RO
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File /oname=${APP_NAME}.exe "${APP_STAGE}\${APP_NAME}.exe"
  File /oname=使用说明.txt "${__FILEDIR__}\使用说明.txt"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  SetRegView 64
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${APP_NAME}.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" "$\"$INSTDIR\Uninstall.exe$\" /S"
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "DesktopShortcutCreated" 0
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "StartMenuShortcutCreated" 0
SectionEnd

Section /o "开始菜单快捷方式" SEC_STARTMENU
  SetShellVarContext current
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_NAME}.exe"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\卸载${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "StartMenuShortcutCreated" 1
SectionEnd

Section /o "桌面快捷方式" SEC_DESKTOP
  SetShellVarContext current
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_NAME}.exe"
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "DesktopShortcutCreated" 1
SectionEnd

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "此安装包适用于 64 位 Windows。"
    Abort
  ${EndIf}
  SetShellVarContext current
  SectionSetFlags ${SEC_STARTMENU} ${SF_SELECTED}
  SectionSetFlags ${SEC_DESKTOP} ${SF_SELECTED}
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  SetRegView 64
  ReadRegDWORD $DesktopShortcutCreated HKCU "${UNINSTALL_KEY}" "DesktopShortcutCreated"
  ReadRegDWORD $StartMenuShortcutCreated HKCU "${UNINSTALL_KEY}" "StartMenuShortcutCreated"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  ${If} $StartMenuShortcutCreated == 1
    Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\卸载${APP_NAME}.lnk"
    RMDir "$SMPROGRAMS\${APP_NAME}"
  ${EndIf}
  ${If} $DesktopShortcutCreated == 1
    Delete "$DESKTOP\${APP_NAME}.lnk"
  ${EndIf}

  ${If} $RemoveUserData == ${BST_CHECKED}
    RMDir /r "$LOCALAPPDATA\${APP_NAME}"
  ${EndIf}

  Delete "$INSTDIR\${APP_NAME}.exe"
  Delete "$INSTDIR\使用说明.txt"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
