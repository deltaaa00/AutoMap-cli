/**
 * Video Studio Tailwind configuration. Load immediately after the Tailwind CDN
 * script in the document head, before the workspace markup is parsed.
 */
tailwind.config = {
    darkMode: 'class',
    theme: {
        extend: {
            fontFamily: {
                sans: ['IBM Plex Sans', 'sans-serif'],
                display: ['IBM Plex Sans', 'sans-serif'],
                mono: ['JetBrains Mono', 'monospace'],
            },
            colors: {
                accent: {50:'#f0f7f2',100:'#dfede3',200:'#bcd5c4',300:'#8fb69a',400:'#5e936d',500:'#3d7b51',600:'#28623c',700:'#205232',800:'#1c432a',900:'#183823',950:'#102619'},
                slate: {50:'#fafafa',100:'#ededed',200:'#d4d4d7',300:'#bcbcc2',400:'#a1a1a6',500:'#85858b',600:'#717177',700:'#39393e',800:'#2b2b2f',850:'#202023',900:'#19191b',950:'#141415'}
            }
        }
    }
}
