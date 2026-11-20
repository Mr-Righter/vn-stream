import type { Edit } from '../../api/generated/types.gen.js';
import { useLayoutEffect, useRef, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Input,
  Modal,
  Pagination,
  Space,
  Switch,
  Typography,
} from 'antd';
import { Pencil, Save, X } from 'lucide-react';
import { contentAPI, useContent } from '../../api/content-client.js';
import type { SaveEntry, SaveField, SaveVariables } from '../../api/types.js';
import { registerDraftGuard } from '../../app/content-navigation.js';
import { t, messageOf, type MessageInput } from '../../ui/preferences-store.js';
import { FilterLabel, Segmented } from '../../ui/segments.js';
import { useViewLifetime } from './save-actions.js';

type Draft = { revision: string; fields: Record<string, SaveField> };

export function SaveEditor({
  path,
  item,
  refresh,
  onSaved,
}: {
  path: string;
  item: SaveEntry;
  refresh: number;
  onSaved: () => void;
}) {
  const [search, searchSet] = useState('');
  const [kind, kindSet] = useState('');
  const [page, pageSet] = useState(1);
  const [discardRevision, advanceDiscardRevision] = useState(0);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editing, editingSet] = useState<{ field: SaveField; revision: string } | null>(null);
  const [input, inputSet] = useState('');
  const [busy, busySet] = useState(false);
  const [error, errorSet] = useState<MessageInput | null>(null);
  const [invalid, invalidSet] = useState(false);
  const writing = useRef(false);
  const isActive = useViewLifetime();
  const { modal, message } = App.useApp();
  const view = useContent<SaveVariables>(
    `${path}/variables?${new URLSearchParams({ search, kind, page: String(page) })}`,
    refresh + discardRevision,
  );
  const dirty = draft !== null;
  const hasUnappliedInput = editing !== null && input !== String(editing.field.value);
  const hasUnsavedChanges = dirty || hasUnappliedInput;

  useLayoutEffect(() => {
    if (!hasUnsavedChanges) return;
    let pending: Promise<boolean> | null = null;
    let confirm: ReturnType<typeof modal.confirm> | null = null;
    let complete: (accepted: boolean) => void;
    const cleanup = registerDraftGuard(() => {
      if (writing.current) return Promise.resolve(false);
      if (pending) return pending;
      pending = new Promise<boolean>((resolve) => {
        complete = (accepted) => {
          confirm = null;
          pending = null;
          if (accepted) {
            setDraft(null);
            editingSet(null);
          }
          resolve(accepted);
        };
        confirm = modal.confirm({
          title: t('saves.unsaved'),
          onOk: () => complete(true),
          onCancel: () => complete(false),
        });
      });
      return pending;
    });
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => {
      cleanup();
      confirm?.destroy();
      if (pending) complete(false);
      window.removeEventListener('beforeunload', warn);
    };
  }, [hasUnsavedChanges, modal]);

  function setField(field: SaveField, value: string | boolean, revision: string) {
    setDraft((previous) => ({
      revision: previous?.revision ?? revision,
      fields: { ...previous?.fields, [field.id]: { ...field, value } },
    }));
  }

  async function save() {
    if (!draft || !writable || writing.current) return;
    writing.current = true;
    busySet(true);
    errorSet(null);
    try {
      const body: Edit = {
        revision: draft.revision,
        changes: Object.values(draft.fields).map(({ id, type, value }) => ({ id, type, value })),
      };
      await contentAPI(`${path}/variables`, body, 'PATCH');
      onSaved();
      if (isActive()) {
        setDraft(null);
        void message.success(t('saves.saved'));
      }
    } catch (error) {
      if (isActive()) errorSet(messageOf(error));
    } finally {
      writing.current = false;
      if (isActive()) busySet(false);
    }
  }

  function editValue() {
    if (!editing) return;
    const { field, revision } = editing;
    if (
      field.type === 'number' &&
      !(
        (field.numeric_type === 'integer'
          ? /^[+-]?\d+$/
          : /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/
        ).test(input) &&
        (field.numeric_type === 'integer' || Number.isFinite(Number(input)))
      )
    ) {
      invalidSet(true);
      return;
    }
    setField(field, input, revision);
    editingSet(null);
  }
  const value = view.value;
  const writable = !!value?.editable && value.writable && !view.loading && !view.error && !busy;
  const count = Object.keys(draft?.fields ?? {}).length;
  return (
    <div className="vn-save-editor">
      {item.kind === 'persistent' && <Alert type="info" title={t('saves.globalNote')} />}
      {(error || view.error) && <Alert type="error" showIcon title={t(error ?? view.error!)} />}
      {value?.reason && <Alert type="warning" title={t(value.reason)} />}
      {value && !value.writable && <Alert type="warning" title={t('saves.stopFirst')} />}
      <Input.Search
        allowClear
        value={search}
        placeholder={t('saves.search')}
        aria-label={t('saves.search')}
        onChange={(e) => {
          searchSet(e.target.value);
          pageSet(1);
        }}
      />
      <Segmented
        pills
        aria-label={t('saves.edit')}
        value={kind}
        onChange={(v) => {
          kindSet(String(v));
          pageSet(1);
        }}
        options={['', 'boolean', 'text', 'number'].map((type) => ({
          value: type,
          label: (
            <FilterLabel
              label={t(type ? `saves.${type}` : 'saves.all')}
              count={
                type
                  ? (value?.counts[type] ?? 0)
                  : Object.values(value?.counts ?? {}).reduce((a, b) => a + b, 0)
              }
            />
          ),
        }))}
      />
      <Card size="small" className="vn-variable-list" loading={view.loading && !value}>
        {value?.fields.map((field) => {
          const display = draft?.fields[field.id] ?? field;
          return (
            <div className="vn-variable-row" key={field.id}>
              <Typography.Text className="vn-variable-name" title={field.name}>
                {field.name}
              </Typography.Text>
              {field.type === 'boolean' ? (
                <Switch
                  aria-label={field.name}
                  checked={display.value === true}
                  disabled={!writable}
                  onChange={(v) => setField(field, v, value.revision)}
                />
              ) : (
                <Button
                  type="text"
                  className="vn-variable-value"
                  disabled={!writable}
                  onClick={() => {
                    editingSet({ field: display, revision: value.revision });
                    inputSet(String(display.value));
                    invalidSet(false);
                  }}
                >
                  <span>{String(display.value) || '—'}</span>
                  <Pencil size={14} />
                </Button>
              )}
            </div>
          );
        })}
        {value?.total === 0 && <Empty description={t('saves.emptyFields')} />}
      </Card>
      <Pagination
        current={page}
        total={value?.total ?? 0}
        pageSize={100}
        showSizeChanger={false}
        onChange={pageSet}
      />
      <div className="vn-save-editor-actions">
        <Typography.Text>{t('saves.pending', { count: count })}</Typography.Text>
        <Space wrap>
          <Button
            aria-label={t('saves.discard')}
            disabled={!dirty || busy}
            icon={<X size={16} />}
            onClick={() =>
              modal.confirm({
                title: t('saves.unsaved'),
                onOk: () => {
                  setDraft(null);
                  advanceDiscardRevision((value) => value + 1);
                },
              })
            }
          >
            {t('saves.discardAction')}
          </Button>
          <Button
            aria-label={t('saves.saveChanges')}
            type="primary"
            disabled={!dirty || !writable}
            loading={busy}
            icon={<Save size={16} />}
            onClick={() => void save()}
          >
            {t('saves.saveAction')}
          </Button>
        </Space>
      </div>
      <Modal
        open={!!editing}
        title={t('saves.editValue', { name: editing?.field.name ?? '' })}
        onCancel={() => editingSet(null)}
        onOk={editValue}
      >
        <Input.TextArea
          autoFocus
          value={input}
          onChange={(e) => {
            inputSet(e.target.value);
            invalidSet(false);
          }}
          aria-label={t('saves.value')}
          autoSize={{ minRows: editing?.field.type === 'number' ? 1 : 3, maxRows: 10 }}
        />
        {invalid && <Alert type="error" title={t('saves.invalidNumber')} />}
      </Modal>
    </div>
  );
}
