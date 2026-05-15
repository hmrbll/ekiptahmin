/**
 * ekiptahmin.com - Tailwind config
 * Sunday Pitch palette (v01), light-only.
 *
 * Brand color scales (pitch, clay, stone, success, warning, danger) are
 * fixed hex values; semantic tokens (page, surface, fg, line, primary,
 * accent) point at the CSS variables defined in src/styles.css.
 */

/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
        '../templates/**/*.html',
        '../../templates/**/*.html',
        '../../**/templates/**/*.html',
    ],
    theme: {
        extend: {
            colors: {
                pitch: {
                    50:  '#EEF7F0',
                    100: '#D6ECDB',
                    200: '#ADD7B7',
                    300: '#82C091',
                    400: '#58A86D',
                    500: '#2E6B3F',
                    600: '#265A35',
                    700: '#1F4A2D',
                    800: '#183A24',
                    900: '#112B1B',
                    950: '#091610',
                    DEFAULT: '#2E6B3F',
                },
                clay: {
                    50:  '#FBF1EB', 100: '#F4DDCC', 200: '#E9BB9C', 300: '#DD986B',
                    400: '#D17F4F', 500: '#C2683E', 600: '#A35630', 700: '#844425',
                    800: '#65331B', 900: '#4A2614', 950: '#2A150B', DEFAULT: '#C2683E',
                },
                stone: {
                    50:  '#F6F5F3', 100: '#EAE9E6', 200: '#D5D4CF', 300: '#BAB9B3',
                    400: '#8E8D87', 500: '#6F7670', 600: '#565C57', 700: '#3F4541',
                    800: '#2A2F2B', 900: '#1B201C', 950: '#0E110F',
                },
                success: {
                    50:  '#ECF8EF', 100: '#CFEDD6', 200: '#A0DCAE', 300: '#6FC885',
                    400: '#50B66B', 500: '#3FA45E', 600: '#2F8649', 700: '#246838',
                    800: '#1A4D29', 900: '#11331B', 950: '#07190D', DEFAULT: '#3FA45E',
                },
                warning: {
                    50:  '#FDF6E7', 100: '#FAE9C0', 200: '#F4D285', 300: '#EAB94B',
                    400: '#DCA635', 500: '#D89A2A', 600: '#B07F1F', 700: '#886218',
                    800: '#604611', 900: '#3D2D0B', 950: '#1F1605', DEFAULT: '#D89A2A',
                },
                danger: {
                    50:  '#FCEEEC', 100: '#F8D5CF', 200: '#EFA89D', 300: '#E47B6B',
                    400: '#D55C4A', 500: '#C84132', 600: '#A33125', 700: '#7E251B',
                    800: '#5C1A14', 900: '#3D110D', 950: '#1E0806', DEFAULT: '#C84132',
                },
                chalk: '#F6F1E4',
                cream: '#EFE7D2',
                ink:   '#1B201C',

                // Semantic tokens — auto-swap via @media (prefers-color-scheme).
                // Wrapped in rgb(... / <alpha-value>) so Tailwind opacity
                // modifiers (e.g. bg-primary/10) work on CSS variables.
                page:             'rgb(var(--color-bg) / <alpha-value>)',
                'page-subtle':    'rgb(var(--color-bg-subtle) / <alpha-value>)',
                surface:          'rgb(var(--color-surface) / <alpha-value>)',
                'surface-raised': 'rgb(var(--color-surface-raised) / <alpha-value>)',
                fg:               'rgb(var(--color-text) / <alpha-value>)',
                'fg-muted':       'rgb(var(--color-text-muted) / <alpha-value>)',
                'fg-soft':        'rgb(var(--color-text-soft) / <alpha-value>)',
                'fg-inverse':     'rgb(var(--color-text-inverse) / <alpha-value>)',
                line:             'rgb(var(--color-border) / <alpha-value>)',
                'line-strong':    'rgb(var(--color-border-strong) / <alpha-value>)',
                divider:          'rgb(var(--color-divider) / <alpha-value>)',
                primary:          'rgb(var(--color-primary) / <alpha-value>)',
                'primary-hover':  'rgb(var(--color-primary-hover) / <alpha-value>)',
                'primary-active': 'rgb(var(--color-primary-active) / <alpha-value>)',
                'on-primary':     'rgb(var(--color-primary-fg) / <alpha-value>)',
                accent:           'rgb(var(--color-accent) / <alpha-value>)',
                'accent-hover':   'rgb(var(--color-accent-hover) / <alpha-value>)',
                'on-accent':      'rgb(var(--color-accent-fg) / <alpha-value>)',
            },
            fontFamily: {
                display: ['"Bricolage Grotesque"', '"Inter Tight"', 'system-ui', 'sans-serif'],
                sans:    ['Geist', 'system-ui', '-apple-system', '"Segoe UI"', 'sans-serif'],
                mono:    ['"JetBrains Mono"', 'ui-monospace', 'Menlo', 'monospace'],
            },
            borderRadius: {
                xs: '4px',
                sm: '6px',
                md: '10px',
                lg: '14px',
                xl: '20px',
            },
            boxShadow: {
                sm: '0 1px 0 rgba(27,32,28,0.04)',
                md: '0 4px 12px rgba(27,32,28,0.08)',
                lg: '0 12px 32px rgba(27,32,28,0.12)',
            },
        },
    },
    plugins: [
        require('@tailwindcss/forms'),
        require('@tailwindcss/typography'),
        require('@tailwindcss/aspect-ratio'),
    ],
}
