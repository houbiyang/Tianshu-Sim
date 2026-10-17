import { Container, getContainer } from "@cloudflare/containers";

/**
 * GenTactix 的容器类。
 *
 * 一个 Container 类实例 = 一个容器进程。这里我们只跑一个全局实例来托管
 * Streamlit 进程 —— Streamlit 本身按浏览器 session 隔离状态，单进程即可
 * 服务多个访问者，不需要（也不应该）为每个用户开一个容器。
 */
export class GentactixContainer extends Container {
	/** Streamlit 监听端口，必须与 Dockerfile 里的 --server.port 保持一致 */
	defaultPort = 8501;

	/**
	 * 空闲多久后休眠。容器一旦休眠，磁盘会被清空，下次请求付一次冷启动代价
	 * （拉起 VM + 启动 Python 进程，通常十几秒）。
	 *
	 * 页面开着时 Streamlit 会持续心跳，不会触发休眠；关掉页面后才开始计时。
	 * 想更省成本就调短（如 "15m"），想避免重访时冷启动就调长（如 "6h"）。
	 */
	sleepAfter = "1h";
}

interface Env {
	GENTACTIX: DurableObjectNamespace<GentactixContainer>;
}

export default {
	async fetch(request: Request, env: Env): Promise<Response> {
		// 固定实例名 => 全局单实例。所有 HTTP 与 WebSocket 请求都汇聚到
		// 同一个 Streamlit 进程，session 才能被正确识别与保持。
		const container = getContainer(env.GENTACTIX, "gentactix-main");

		// Container.fetch 会自动转发 WebSocket 升级请求，无需手工设置 Upgrade 头。
		// Streamlit 的实时交互（_stcore/stream）就是靠这条链路。
		return container.fetch(request);
	},
};
