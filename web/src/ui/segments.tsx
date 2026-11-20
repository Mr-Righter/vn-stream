import { useLayoutEffect, useRef } from 'react';
import { Segmented as AntSegmented, type SegmentedProps } from 'antd';

export function Segmented(props: SegmentedProps & { pills?: boolean }) {
  const { pills, ...rest } = props;
  const root = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = root.current;
    if (!element) return;
    const adjust = () => {
      const active = element.querySelector('.ant-segmented-item-selected');
      if (!active) return;
      const box = active.getBoundingClientRect(),
        view = element.getBoundingClientRect();
      if (box.left < view.left) element.scrollLeft += box.left - view.left - 4;
      else if (box.right > view.right) element.scrollLeft += box.right - view.right + 4;
    };
    adjust();
    const observer = new ResizeObserver(adjust);
    observer.observe(element);
    const group = element.querySelector('.ant-segmented-group');
    if (group) observer.observe(group);
    return () => observer.disconnect();
  }, [props.value]);
  return (
    <div ref={root} className={`vn-segment-scroll${pills ? ' vn-filter-strip' : ''}`}>
      <AntSegmented
        {...rest}
        className={`${props.className ?? ''} ${pills ? 'vn-pill-segments' : ''}`}
      />
    </div>
  );
}

export function FilterLabel({ label, count }: { label: string; count: number }) {
  return (
    <span className="vn-filter-label">
      <span>{label}</span>
      <span className="vn-filter-count">{count}</span>
    </span>
  );
}
