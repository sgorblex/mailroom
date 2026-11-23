import { useEffect, useId, useImperativeHandle, useRef, useState, type ReactElement, type ReactNode, type Ref } from 'react'
import { useTranslation } from 'react-i18next'
import { Extension, EditorContent, Node, useEditor, useEditorState, type Editor } from '@tiptap/react'
import { DOMParser as SchemaParser, type Node as DocNode } from '@tiptap/pm/model'
import StarterKit from '@tiptap/starter-kit'
import Image, { type ImageOptions } from '@tiptap/extension-image'
import Link from '@tiptap/extension-link'
import Underline from '@tiptap/extension-underline'
import Placeholder from '@tiptap/extension-placeholder'
import { Bold, Italic, Link as LinkIcon, List, ListOrdered, TextQuote, Type, Underline as UnderlineIcon } from 'lucide-react'

import { messagePartUrl } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { Dialog } from '@/components/ui/dialog.tsx'
import { Input } from '@/components/ui/input.tsx'

import { isPart, type InlineItem } from './compose-form.ts'
import { MAIL_NS } from './i18n.ts'
import { linkTarget, SAFE_LINK, SHOWN_IMAGES } from './rich-text.ts'
import { SIGNATURE_MARK } from './signature.ts'

export interface RichEditorHandle {
  focus: (where?: 'start') => void
  /**
   * The signature `before` (HTML; `null` for none) swapped for `after`, where the signature's
   * block still holds `before` as it was put there. A message without the block gets one at its
   * end only with `append`, and only if it had no signature to lose.
   */
  swapSignature: (before: string | null, after: string | null, append: boolean) => void
}

/** The block the signature goes in, marked so the server keeps it; empty for none. */
const SignatureBlock = Node.create({
  name: 'signature',
  group: 'block',
  content: 'block*',
  defining: true,
  parseHTML: () => [{ tag: `div[${SIGNATURE_MARK}]` }],
  renderHTML: () => ['div', { [SIGNATURE_MARK]: '' }, 0],
})

/** The signature's HTML as a block of the editor's: parsed in an inert document, so nothing in it loads. */
function signatureNode(editor: Editor, html: string | null): DocNode | null {
  const parsed = new window.DOMParser().parseFromString(`<div ${SIGNATURE_MARK}="">${html ?? ''}</div>`, 'text/html')
  const node = SchemaParser.fromSchema(editor.schema).parse(parsed.body).firstChild
  return node?.type.name === 'signature' ? node : null
}

function swapSignature(editor: Editor, before: string | null, after: string | null, append: boolean): void {
  const { doc } = editor.state
  const next = signatureNode(editor, after)
  if (next === null) {
    return
  }
  for (let index = 0, at = 0; index < doc.childCount; index += 1) {
    const current = doc.child(index)
    if (current.type.name === 'signature') {
      const previous = signatureNode(editor, before)
      if (previous !== null && current.content.eq(previous.content)) {
        editor.view.dispatch(editor.state.tr.replaceWith(at, at + current.nodeSize, next))
      }
      return
    }
    at += current.nodeSize
  }
  if (append && before === null && after !== null) {
    editor.view.dispatch(editor.state.tr.insert(doc.content.size, next))
  }
}

function dataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        resolve(reader.result)
      }
    }
    reader.onerror = () => {
      reject(reader.error ?? new Error('unreadable'))
    }
    reader.readAsDataURL(file)
  })
}

type InlineImageOptions = ImageOptions & { resolve: (contentId: string) => string | undefined }

/**
 * An image the message shows in place: only `cid:` ones parse, so nothing pasted reaches out. Its
 * HTML keeps the `cid:`; what the editor shows is the upload or the part it names.
 */
const InlineImage = Image.extend<InlineImageOptions>({
  addOptions() {
    return { ...(this.parent?.() as ImageOptions), resolve: () => undefined }
  },
  parseHTML() {
    return [{ tag: 'img[src^="cid:"]' }]
  },
  addNodeView() {
    const { resolve } = this.options
    return ({ node }) => {
      const dom = document.createElement('img')
      const url = resolve(String(node.attrs['src']).slice('cid:'.length))
      if (url !== undefined) {
        dom.src = url
      }
      dom.alt = typeof node.attrs['alt'] === 'string' ? node.attrs['alt'] : ''
      return { dom }
    }
  },
})

function ToolButton({
  label,
  pressed,
  disabled,
  onClick,
  children,
}: {
  label: string
  pressed?: boolean
  disabled: boolean
  onClick: () => void
  children: ReactNode
}): ReactElement {
  return (
    <Button
      type="button"
      variant={pressed === true ? 'secondary' : 'ghost'}
      size="icon-sm"
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      disabled={disabled}
      // The editor keeps its selection: a click here must not take focus from it.
      onMouseDown={(event) => {
        event.preventDefault()
      }}
      onClick={onClick}
    >
      {children}
    </Button>
  )
}

function LinkDialog({ editor, onClose }: { editor: Editor; onClose: () => void }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const inputId = useId()
  const errorId = useId()
  const current = editor.getAttributes('link')['href'] as unknown
  const [value, setValue] = useState(typeof current === 'string' ? current : '')
  const [invalid, setInvalid] = useState(false)

  function apply(): void {
    if (value.trim() === '') {
      editor.chain().focus().extendMarkRange('link').unsetLink().run()
      onClose()
      return
    }
    const href = linkTarget(value)
    if (href === null) {
      setInvalid(true)
      return
    }
    const chain = editor.chain().focus().extendMarkRange('link')
    if (editor.state.selection.empty && !editor.isActive('link')) {
      chain.insertContent({ type: 'text', text: value.trim(), marks: [{ type: 'link', attrs: { href } }] }).run()
    } else {
      chain.setLink({ href }).run()
    }
    onClose()
  }

  return (
    <Dialog label={t('compose.link')} onCancel={onClose}>
      {/* Not a form: the dialog sits inside the composer's. */}
      <div className="flex flex-col gap-2">
        <label htmlFor={inputId} className="text-sm">
          {t('compose.linkAddress')}
        </label>
        <Input
          id={inputId}
          type="text"
          inputMode="url"
          value={value}
          aria-invalid={invalid}
          aria-describedby={invalid ? errorId : undefined}
          onChange={(event) => {
            setValue(event.target.value)
            setInvalid(false)
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              apply()
            }
          }}
        />
        {invalid && (
          <p id={errorId} role="alert" className="text-sm text-destructive">
            {t('compose.linkInvalid')}
          </p>
        )}
        <div className="mt-2 flex justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            {t('compose.cancel')}
          </Button>
          <Button type="button" size="sm" onClick={apply}>
            {value.trim() === '' ? t('compose.removeLink') : t('compose.applyLink')}
          </Button>
        </div>
      </div>
    </Dialog>
  )
}

/**
 * The body in rich text (TipTap): bold, italic, underline, links, lists and quotes, with images
 * pasted or dropped in going in place as inline parts. Its HTML is the editor's own; the server
 * sanitises it again before anything is stored or sent.
 */
export function RichEditor({
  ref,
  focusStart,
  labelId,
  html,
  inline,
  locked,
  onChange,
  onImages,
  onFiles,
  onPlainText,
  placeholder,
}: {
  ref?: Ref<RichEditorHandle>
  /** Focused at its start once created, as a reply is. */
  focusStart: boolean
  labelId: string
  /** The body it opens on; later changes to it are its own. */
  html: string
  /** The inline parts it opens with. */
  inline: InlineItem[]
  locked: boolean
  onChange: (html: string, text: string) => void
  /** Images pasted or dropped in: the Content-ID of each as an inline part, `null` when they cannot be added. */
  onImages: (files: File[]) => string[] | null
  /** Other files dropped in, for the attachments. */
  onFiles: (files: File[]) => void
  /** The switch to plain text; none without. */
  onPlainText?: () => void
  /** What the empty editor shows; the message body's by default. */
  placeholder?: string
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const bodyId = useId()
  const [linking, setLinking] = useState(false)
  /** What each `cid:` shows: a part's download, or a file read as a `data:` URL (the CSP allows no `blob:`). */
  const [shown] = useState(() => new Map(inline.filter(isPart).map((item) => [item.contentId, messagePartUrl(item.ref, item.part)])))
  const latest = useRef({ onChange, onImages, onFiles })
  useEffect(() => {
    latest.current = { onChange, onImages, onFiles }
  })
  // A save moves the parts to the new version's ref, and the old one is deleted with its version.
  useEffect(() => {
    const parts = new Map(inline.filter(isPart).map((item) => [item.contentId, messagePartUrl(item.ref, item.part)]))
    for (const [contentId, url] of shown) {
      if (!url.startsWith('data:') && !parts.has(contentId)) {
        shown.delete(contentId)
      }
    }
    for (const [contentId, url] of parts) {
      if (!(shown.get(contentId)?.startsWith('data:') ?? false)) {
        shown.set(contentId, url)
      }
    }
  }, [inline, shown])

  const editor = useEditor({
    extensions: [
      // No trailing paragraph: it would change a reply's body before anything is typed.
      StarterKit.configure({ link: false, underline: false, heading: false, code: false, codeBlock: false, trailingNode: false }),
      Underline,
      Link.configure({
        openOnClick: false,
        defaultProtocol: 'https',
        isAllowedUri: (url, { defaultValidate }) => defaultValidate(url) && SAFE_LINK.test(url),
      }),
      InlineImage.configure({ inline: true, resolve: (contentId) => shown.get(contentId) }),
      SignatureBlock,
      Placeholder.configure({ placeholder: placeholder ?? t('compose.bodyPlaceholder') }),
      // A reply's quote stays a cite, as mail clients mark one.
      Extension.create({
        name: 'citeQuote',
        addGlobalAttributes: () => [
          {
            types: ['blockquote'],
            attributes: {
              type: { default: null, parseHTML: (element) => (element.getAttribute('type') === 'cite' ? 'cite' : null) },
            },
          },
        ],
      }),
      Extension.create({
        name: 'linkShortcut',
        addKeyboardShortcuts: () => ({
          'Mod-k': () => {
            setLinking(true)
            return true
          },
        }),
      }),
    ],
    content: html,
    editable: !locked,
    autofocus: focusStart ? 'start' : false,
    editorProps: {
      attributes: {
        id: bodyId,
        role: 'textbox',
        'aria-multiline': 'true',
        'aria-labelledby': labelId,
        class: 'rich-body min-h-40 flex-1 px-3 py-2 text-base outline-none md:text-sm',
      },
      handlePaste: (_view, event) => {
        // A word processor puts a picture of the copied text next to it: the text is what was meant.
        if (event.clipboardData?.types.includes('text/html') === true) {
          return false
        }
        const files = [...(event.clipboardData?.files ?? [])]
        return files.length > 0 && take(files, null)
      },
      handleDrop: (view, event, _slice, moved) => {
        const files = [...(event.dataTransfer?.files ?? [])]
        if (moved || files.length === 0) {
          return false
        }
        return take(files, view.posAtCoords({ left: event.clientX, top: event.clientY })?.pos ?? null)
      },
    },
    onUpdate: ({ editor: changed }) => {
      // The text only says whether anything was written: the server derives the text part from
      // the HTML. A signature alone is nothing written, so a message left with only it is discarded.
      const text = changed.getText({ blockSeparator: '\n', textSerializers: { signature: () => '' } })
      latest.current.onChange(changed.getHTML(), text.trim() === '' ? '' : text)
    },
  })

  /** Files pasted or dropped at `at` (the selection when `null`): images in place, the rest attached. */
  function take(files: File[], at: number | null): boolean {
    const images = files.filter((file) => SHOWN_IMAGES.has(file.type))
    const others = files.filter((file) => !SHOWN_IMAGES.has(file.type))
    if (others.length > 0) {
      latest.current.onFiles(others)
    }
    const contentIds = images.length > 0 ? latest.current.onImages(images) : null
    if (contentIds !== null) {
      place(images, contentIds, at).catch(() => undefined)
    }
    return true
  }

  async function place(images: File[], contentIds: string[], at: number | null): Promise<void> {
    const placed = await Promise.all(
      images.map(async (file, index) => {
        const contentId = contentIds[index] ?? ''
        shown.set(contentId, await dataUrl(file))
        return { type: 'image', attrs: { src: `cid:${contentId}`, alt: file.name } }
      }),
    )
    // The composer may have closed, or the text changed, while the files were read.
    if (editor.isDestroyed) {
      return
    }
    const chain = editor.chain().focus()
    const end = editor.state.doc.content.size
    void (at === null ? chain.insertContent(placed) : chain.insertContentAt(Math.min(at, end), placed)).run()
  }

  useEffect(() => {
    const apply = (): void => {
      editor.setEditable(!locked, false)
      editor.view.dom.setAttribute('aria-readonly', String(locked))
    }
    // The view exists once the editor is mounted, which is not before this runs the first time.
    if (editor.isInitialized) {
      apply()
      return undefined
    }
    editor.on('create', apply)
    return () => {
      editor.off('create', apply)
    }
  }, [editor, locked])

  useImperativeHandle(ref, () => ({
    focus: (where) => {
      editor.commands.focus(where ?? null)
    },
    swapSignature: (before, after, append) => {
      swapSignature(editor, before, after, append)
    },
  }))

  const active = useEditorState({
    editor,
    selector: ({ editor: current }) => ({
      bold: current.isActive('bold'),
      italic: current.isActive('italic'),
      underline: current.isActive('underline'),
      link: current.isActive('link'),
      bulletList: current.isActive('bulletList'),
      orderedList: current.isActive('orderedList'),
      blockquote: current.isActive('blockquote'),
    }),
  })

  const run = (command: (chain: ReturnType<Editor['chain']>) => ReturnType<Editor['chain']>) => () => {
    command(editor.chain().focus()).run()
  }

  return (
    <div className="flex flex-1 flex-col rounded-md border border-input shadow-xs focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring">
      <div role="toolbar" aria-label={t('compose.formatting')} aria-controls={bodyId} className="flex flex-wrap items-center gap-0.5 border-b p-1">
        <ToolButton label={t('compose.bold')} pressed={active.bold} disabled={locked} onClick={run((chain) => chain.toggleBold())}>
          <Bold aria-hidden="true" />
        </ToolButton>
        <ToolButton label={t('compose.italic')} pressed={active.italic} disabled={locked} onClick={run((chain) => chain.toggleItalic())}>
          <Italic aria-hidden="true" />
        </ToolButton>
        <ToolButton label={t('compose.underline')} pressed={active.underline} disabled={locked} onClick={run((chain) => chain.toggleUnderline())}>
          <UnderlineIcon aria-hidden="true" />
        </ToolButton>
        <ToolButton
          label={t('compose.link')}
          pressed={active.link}
          disabled={locked}
          onClick={() => {
            setLinking(true)
          }}
        >
          <LinkIcon aria-hidden="true" />
        </ToolButton>
        <ToolButton label={t('compose.bulletList')} pressed={active.bulletList} disabled={locked} onClick={run((chain) => chain.toggleBulletList())}>
          <List aria-hidden="true" />
        </ToolButton>
        <ToolButton label={t('compose.orderedList')} pressed={active.orderedList} disabled={locked} onClick={run((chain) => chain.toggleOrderedList())}>
          <ListOrdered aria-hidden="true" />
        </ToolButton>
        <ToolButton label={t('compose.quote')} pressed={active.blockquote} disabled={locked} onClick={run((chain) => chain.toggleBlockquote())}>
          <TextQuote aria-hidden="true" />
        </ToolButton>
        {onPlainText !== undefined && (
          <Button type="button" variant="ghost" size="xs" className="ml-auto" disabled={locked} onClick={onPlainText}>
            <Type aria-hidden="true" />
            {t('compose.plainText')}
          </Button>
        )}
      </div>
      <EditorContent editor={editor} className="flex flex-1 flex-col" />
      {linking && (
        <LinkDialog
          editor={editor}
          onClose={() => {
            setLinking(false)
          }}
        />
      )}
    </div>
  )
}
