/**
 * Bilingual (Traditional Chinese + English) copy for the seminar survey,
 * mirroring the seminar's Google Form. Option values are what
 * `validate_answers` in `hermes_cli/seminar_survey.py` accepts.
 */
import type { SeminarGrade, SeminarNextStep, SeminarShare } from "@/types";

export const SEMINAR_TITLE = {
  en: "How to help our children to flourish with AI?",
  zh: "如何幫助孩子在AI時代茁壯成長？",
};

export const GRADES: { value: SeminarGrade; label: string }[] = [
  { value: "P1", label: "P1 小一" },
  { value: "P2", label: "P2 小二" },
  { value: "P3", label: "P3 小三" },
  { value: "P4", label: "P4 小四" },
  { value: "P5", label: "P5 小五" },
  { value: "P6", label: "P6 小六" },
];

export const SHARE_OPTIONS: { value: SeminarShare; label: string }[] = [
  { value: "named", label: "Yes, with my name 可以，並附上我的名字" },
  {
    value: "anonymous",
    label:
      "Yes, anonymously (e.g. “Parent of a P4 student”) 可以，但請匿名（例如「小四學生家長」）",
  },
  { value: "no", label: "Please don't share 請不要分享" },
];

export const NEXT_STEPS: { value: SeminarNextStep; label: string }[] = [
  { value: "enrol", label: "Enrol my child in an AI&I class 為孩子報讀 AI&I 課程" },
  { value: "workshop", label: "Join a hands-on parent workshop 參加家長實作工作坊" },
  {
    value: "consultation",
    label: "Book a free 15-min chat about my child 預約15分鐘免費諮詢",
  },
  { value: "updates", label: "Just keep me updated 請通知我最新消息" },
  { value: "not_now", label: "Not right now 暫時不用" },
];
