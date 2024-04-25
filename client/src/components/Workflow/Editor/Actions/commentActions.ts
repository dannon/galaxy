import { LazyUndoRedoAction, UndoRedoAction } from "@/stores/undoRedoStore";
import type {
    BaseWorkflowComment,
    WorkflowComment,
    WorkflowCommentColor,
    WorkflowCommentStore,
} from "@/stores/workflowEditorCommentStore";
import type { Step, WorkflowStepStore } from "@/stores/workflowStepStore";

class CommentAction extends UndoRedoAction {
    protected store: WorkflowCommentStore;
    protected comment: WorkflowComment;

    constructor(store: WorkflowCommentStore, comment: BaseWorkflowComment) {
        super();
        this.store = store;
        this.comment = structuredClone(comment) as WorkflowComment;
    }
}

export class AddCommentAction extends CommentAction {
    get name() {
        return `add ${this.comment.type} comment`;
    }

    undo() {
        this.store.deleteComment(this.comment.id);
    }

    redo() {
        this.store.addComments([this.comment]);
    }
}

export class DeleteCommentAction extends CommentAction {
    get name() {
        return `delete ${this.comment.type} comment`;
    }

    run() {
        this.store.deleteComment(this.comment.id);
    }

    undo() {
        this.store.addComments([this.comment]);
    }
}

export class ChangeColorAction extends UndoRedoAction {
    private commentId: number;
    private toColor: WorkflowCommentColor;
    private fromColor: WorkflowCommentColor;
    private store: WorkflowCommentStore;
    protected type;

    constructor(store: WorkflowCommentStore, comment: WorkflowComment, color: WorkflowCommentColor) {
        super();
        this.store = store;
        this.commentId = comment.id;
        this.fromColor = comment.color;
        this.toColor = color;
        this.type = comment.type;
    }

    get name() {
        return `change ${this.type} comment color to ${this.toColor}`;
    }

    run() {
        this.store.changeColor(this.commentId, this.toColor);
    }

    undo() {
        this.store.changeColor(this.commentId, this.fromColor);
    }
}

class LazyMutateCommentAction<K extends keyof WorkflowComment> extends LazyUndoRedoAction {
    private commentId: number;
    private startData: WorkflowComment[K];
    private endData: WorkflowComment[K];
    protected type;
    protected applyDataCallback: (commentId: number, data: WorkflowComment[K]) => void;

    constructor(
        comment: WorkflowComment,
        key: K,
        data: WorkflowComment[K],
        applyDataCallback: (commentId: number, data: WorkflowComment[K]) => void
    ) {
        super();
        this.commentId = comment.id;
        this.startData = structuredClone(comment[key]);
        this.endData = structuredClone(data);
        this.applyDataCallback = applyDataCallback;
        this.type = comment.type;
    }

    queued() {
        this.applyDataCallback(this.commentId, this.endData);
    }

    get name() {
        return `change ${this.type} comment`;
    }

    updateData(data: WorkflowComment[K]) {
        this.endData = data;
        this.applyDataCallback(this.commentId, this.endData);
    }

    redo() {
        this.applyDataCallback(this.commentId, this.endData);
    }

    undo() {
        this.applyDataCallback(this.commentId, this.startData);
    }
}

export class LazyChangeDataAction extends LazyMutateCommentAction<"data"> {
    constructor(store: WorkflowCommentStore, comment: WorkflowComment, data: WorkflowComment["data"]) {
        const callback = store.changeData;
        super(comment, "data", data, callback);
    }
}

export class LazyChangePositionAction extends LazyMutateCommentAction<"position"> {
    constructor(store: WorkflowCommentStore, comment: WorkflowComment, position: [number, number]) {
        const callback = store.changePosition;
        super(comment, "position", position, callback);
    }

    get name() {
        return `change ${this.type} comment position`;
    }
}

export class LazyChangeSizeAction extends LazyMutateCommentAction<"size"> {
    constructor(store: WorkflowCommentStore, comment: WorkflowComment, size: [number, number]) {
        const callback = store.changeSize;
        super(comment, "size", size, callback);
    }

    get name() {
        return `resize ${this.type} comment`;
    }
}

type StepWithPosition = Step & { position: NonNullable<Step["position"]> };

export class LazyMoveMultipleAction extends LazyUndoRedoAction {
    private commentStore;
    private stepStore;
    private comments;
    private steps;

    private stepStartOffsets = new Map<number, [number, number]>();
    private commentStartOffsets = new Map<number, [number, number]>();

    private positionFrom;
    private positionTo;

    get name() {
        return "move multiple nodes";
    }

    constructor(
        commentStore: WorkflowCommentStore,
        stepStore: WorkflowStepStore,
        comments: WorkflowComment[],
        steps: StepWithPosition[],
        position: { x: number; y: number },
        positionTo?: { x: number; y: number }
    ) {
        super();
        this.commentStore = commentStore;
        this.stepStore = stepStore;
        this.comments = [...comments];
        this.steps = [...steps];

        this.steps.forEach((step) => {
            this.stepStartOffsets.set(step.id, [step.position.left - position.x, step.position.top - position.y]);
        });

        this.comments.forEach((comment) => {
            this.commentStartOffsets.set(comment.id, [
                comment.position[0] - position.x,
                comment.position[1] - position.y,
            ]);
        });

        this.positionFrom = { ...position };
        this.positionTo = positionTo ? { ...positionTo } : { ...position };
    }

    changePosition(position: { x: number; y: number }) {
        this.setPosition(position);
        this.positionTo = { ...position };
    }

    private setPosition(position: { x: number; y: number }) {
        this.steps.forEach((step) => {
            const stepPosition = { left: 0, top: 0 };
            const offset = this.stepStartOffsets.get(step.id) ?? [0, 0];
            stepPosition.left = position.x + offset[0];
            stepPosition.top = position.y + offset[1];
            this.stepStore.updateStep({ ...step, position: stepPosition });
        });

        this.comments.forEach((comment) => {
            const offset = this.commentStartOffsets.get(comment.id) ?? [0, 0];
            const commentPosition = [position.x + offset[0], position.y + offset[1]] as [number, number];
            this.commentStore.changePosition(comment.id, commentPosition);
        });
    }

    queued() {
        this.setPosition(this.positionTo);
    }

    undo() {
        this.setPosition(this.positionFrom);
    }

    redo() {
        this.setPosition(this.positionTo);
    }
}
