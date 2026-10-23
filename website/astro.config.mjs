import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://cyber-tao.github.io',
  base: '/Codalith',
  trailingSlash: 'always',
  integrations: [
    starlight({
      title: 'Codalith',
      description: 'Versioned source intelligence for AI coding agents. Find code, verify its revision, and cite the exact lines.',
      defaultLocale: 'root',
      locales: {
        root: { label: 'English', lang: 'en' },
        'zh-cn': { label: '简体中文', lang: 'zh-CN' },
      },
      favicon: '/brand/codalith-mark.png',
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/cyber-tao/Codalith' }],
      customCss: ['./src/styles/docs.css'],
      components: {
        SiteTitle: './src/components/SiteTitle.astro',
        Head: './src/components/Head.astro',
      },
      sidebar: [
        { slug: 'start', label: 'Get started', translations: { 'zh-CN': '快速开始' } },
        {
          label: 'Use Codalith',
          translations: { 'zh-CN': '使用 Codalith' },
          items: [
            { slug: 'guides/installation', label: 'Connect your agent', translations: { 'zh-CN': '连接编程代理' } },
            { slug: 'guides/configuration', label: 'Configuration', translations: { 'zh-CN': '配置参考' } },
            { slug: 'guides/mcp-api', label: 'MCP tools & citations', translations: { 'zh-CN': 'MCP 工具与引用' } },
            { slug: 'guides/operations', label: 'Run & troubleshoot', translations: { 'zh-CN': '运行与排障' } },
          ],
        },
        {
          label: 'Understand & evaluate',
          translations: { 'zh-CN': '原理与评估' },
          items: [
            { slug: 'guides/architecture', label: 'Architecture', translations: { 'zh-CN': '架构与不变量' } },
            { slug: 'guides/evaluation', label: 'Evaluation', translations: { 'zh-CN': '评估与验收' } },
          ],
        },
      ],
      editLink: { baseUrl: 'https://github.com/cyber-tao/Codalith/edit/master/website/src/content/docs/' },
      head: [{ tag: 'meta', attrs: { name: 'theme-color', content: '#153d36' } }],
    }),
  ],
});
