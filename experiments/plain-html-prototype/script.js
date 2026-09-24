const API_URL = "http://127.0.0.1:8000";

const fileInput = document.getElementById("file-input");
const uploadButton = document.getElementById("upload-button");
const statusText = document.getElementById("upload-status");

uploadButton.addEventListener("click", async () => {
  const file = fileInput.files[0];
  if (!file) {
    statusText.textContent = "Please choose a file first.";
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  statusText.textContent = "Uploading...";
  try {
    const response = await fetch(`${API_URL}/documents`, {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      throw new Error(`Server returned ${response.status}`);
    }
    const doc = await response.json();
    const kb = (doc.size_bytes / 1024).toFixed(1);
    statusText.textContent = `Received ${doc.filename} (${kb} KB), id ${doc.id}`;
  } catch (error) {
    statusText.textContent = `Upload failed: ${error.message}`;
  }
});