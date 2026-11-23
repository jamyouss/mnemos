import { toast } from 'vue-sonner'

/**
 * Copies `text` and says so. The Clipboard API exists only in secure
 * contexts: over plain HTTP to a remote host `navigator.clipboard` is
 * undefined, and a key shown once must not vanish without a word.
 */
export async function copyToClipboard(text: string, what: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    toast.error('Copy failed — select the text and copy it manually')
    return false
  }
  toast.success(`${what} copied`)
  return true
}
