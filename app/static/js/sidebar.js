// Shared sidebar behavior (used on dashboard, statistics, settings pages)

document.addEventListener('DOMContentLoaded', function () {
    initSidebar();
});

function toggleSidebar() {
    const sidebar = document.getElementById('appSidebar');
    if (!sidebar) return;
    if (window.innerWidth <= 991) {
        sidebar.classList.toggle('mobile-open');
    } else {
        sidebar.classList.toggle('collapsed');
        localStorage.setItem('sidebar-collapsed', sidebar.classList.contains('collapsed') ? '1' : '0');
    }
}

function initSidebar() {
    const sidebar = document.getElementById('appSidebar');
    if (!sidebar) return;
    const saved = localStorage.getItem('sidebar-collapsed');
    if (saved === '1' && window.innerWidth > 991) {
        sidebar.classList.add('collapsed');
    }
    document.addEventListener('click', function (e) {
        if (window.innerWidth <= 991 && sidebar.classList.contains('mobile-open')) {
            if (!sidebar.contains(e.target) && !e.target.closest('.sidebar-toggle-btn')) {
                sidebar.classList.remove('mobile-open');
            }
        }
    });
}

function toggleSidebarSection(header) {
    const card = header.closest('.sidebar-card');
    if (card) card.classList.toggle('collapsed');
}

function showFolderModal() {
    const el = document.getElementById('folderModal');
    if (el) new bootstrap.Modal(el).show();
}

function showTagModal() {
    const el = document.getElementById('tagModal');
    if (el) new bootstrap.Modal(el).show();
}

function renameFolder(folderId, currentName) {
    const newName = prompt('Rename folder:', currentName);
    if (newName && newName !== currentName) {
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '/folders/' + folderId + '/rename';
        const csrf = document.querySelector('input[name="csrf_token"]');
        if (csrf) {
            const c = document.createElement('input');
            c.type = 'hidden'; c.name = 'csrf_token'; c.value = csrf.value;
            form.appendChild(c);
        }
        const inp = document.createElement('input');
        inp.type = 'hidden'; inp.name = 'name'; inp.value = newName;
        form.appendChild(inp);
        document.body.appendChild(form);
        form.submit();
    }
}

// Opens the "Ask AI" modal if present on the current page, otherwise
// navigates to the dashboard where the AI Query modal lives.
function openAIQuery(dashboardUrl) {
    const el = document.getElementById('askAllModal');
    if (el) {
        new bootstrap.Modal(el).show();
    } else {
        window.location.href = dashboardUrl;
    }
}
