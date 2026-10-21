# 校园网部署模板

仓库使用文档示例地址 `192.0.2.10`，不包含真实校园网地址、服务器路径或证书。部署时把实际设置保存在被 Git 忽略的文件中。

## 后端与账号

在部署主机上准备应用目录与独立的仪器控制工程。以下命令在应用目录中运行：

```bash
cp config/server.campus.example.json config/server.json
mkdir -p deploy/local
cp deploy/pump-console-nginx.conf deploy/local/nginx.conf
cp deploy/pump-console.service deploy/local/pump-console.service
```

编辑本机文件：

- `config/server.json`：将 `lab_root` 改为实际控制工程；`access.https_origins` 改为真实 HTTPS 来源。后端保持回环监听，硬件执行保持关闭。只读查询需在确认设备适配后单独启用。
- `deploy/local/nginx.conf`：将示例地址改为校园网 IP，按本机情况设置证书路径；后端转发地址仍为回环地址。
- `deploy/local/pump-console.service`：设置应用目录、Python 路径与服务运行用户。

构建前端，使用 `pump_console.auth add-user` 交互创建首个管理员，再启动后端。服务账户需要对自己的 `data` 目录有写权限。

管理员可在网页的“账号管理”中创建观测员或管理员。新账号、重置后的账号均须在 24 小时内首次登录并更换临时密码。普通用户没有注册入口。

## HTTPS

优先使用学校提供的域名和站点证书。只有内网 IP 时，可以在服务器本地生成专用 CA 和站点证书：

```bash
sudo python3 deploy/generate_tls.py --host YOUR_CAMPUS_IP
```

默认输出目录为 `/etc/pump-console/tls`。脚本不会覆盖已存在的证书材料。私钥留在服务器上，公开的 `ca.crt` 由管理员单独分发，访问者导入并信任后再登录。证书不进入 Git 仓库。

安装本机的 Nginx 配置并执行 `nginx -t`，确认后启用服务。仅在校园网接口的校园网 IP 上允许 TCP 443；保持后端端口不对网络开放。不要使用包含 HTTP 80 的宽泛 Nginx 防火墙配置。

用户级后端需要随服务器启动时，可启用相应用户服务和 linger；Nginx 的网络依赖模板见 `deploy/nginx-campus-network.conf`。

## 隔离的 HTTPS 验证

服务器具备 Nginx、构建产物和证书材料后，可以验证临时后端、临时账号、角色校验和实时推送：

```bash
sudo python3 deploy/verify_campus_access.py --host YOUR_CAMPUS_IP
```

验证工具仅使用临时回环监听器和合成配置，不读取真实账号、实验数据或串口。真实用户登录仍由本人完成。

## 本机 SSH 访问

保留 `allow_ssh_loopback` 时，可以使用自己的 SSH 主机别名转发后端回环端口，访问本机地址。该方式使用独立的会话 Cookie，校园网 HTTPS 则使用 Secure Cookie。

客户端必须有到部署主机的校园网内部路由。跨网段访问受学校网络策略影响。
