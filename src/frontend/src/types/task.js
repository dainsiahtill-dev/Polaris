/**
 * 任务状态枚举
 */
export var TaskStatus;
(function (TaskStatus) {
    TaskStatus["PENDING"] = "pending";
    TaskStatus["IN_PROGRESS"] = "in_progress";
    TaskStatus["COMPLETED"] = "completed";
    TaskStatus["FAILED"] = "failed";
    TaskStatus["BLOCKED"] = "blocked";
    TaskStatus["SUCCESS"] = "success";
})(TaskStatus || (TaskStatus = {}));
