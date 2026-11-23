import { toast } from 'vue-sonner'
import { isRevisionConflict } from './useApi'

/**
 * Reports a failed save. A revision conflict means someone saved in between:
 * nothing of theirs was overwritten, and the only way forward is to reload
 * their version, so the toast stays until dismissed and offers the reload.
 */
export function toastSaveError(e: unknown, reload: () => unknown, title = 'Nothing was saved') {
  if (isRevisionConflict(e)) {
    toast.error('Changed by someone else', {
      description: 'Someone saved this since you opened it. Nothing was overwritten: refresh to get their version, then redo your edit.',
      duration: Infinity,
      action: { label: 'Refresh', onClick: () => reload() },
    })
    return
  }
  toast.error(title, { description: (e as Error).message })
}
