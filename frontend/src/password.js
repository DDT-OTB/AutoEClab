export function passwordError(password, confirmation) {
  const length = Array.from(password).length;
  if (length <= 8 || !/[a-z]/.test(password) || !/[0-9]/.test(password)) return '密码须大于 8 位，并包含小写字母和数字。';
  if (length > 128) return '密码最多支持 128 个字符。';
  if (password !== confirmation) return '两次输入的新密码不一致。';
  return '';
}
